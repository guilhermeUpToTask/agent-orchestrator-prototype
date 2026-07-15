/**
 * src/lib/queries.ts
 *
 * React Query layer: all server state (plan list, plan aggregate, chat
 * history, reference data) is fetched and cached here. Zustand
 * (plannerStore) holds only local UI state — selection, panels, the SSE
 * connection, the rolling event buffer and the live agent log.
 *
 * SSE events invalidate the relevant caches (useSSEBridge), so the UI
 * updates live without HTTP polling.
 */

import { useEffect } from "react";
import {
  QueryClient,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { nanoid } from "nanoid";

import {
  applyEdit,
  activateCycle,
  approveIntentGate,
  approvePlan,
  cancelCycleDraft,
  cancelIntent,
  createAgent,
  createCapability,
  createModel,
  createPlan,
  createProject,
  createProvider,
  deleteAgent,
  deleteCapability,
  deleteModel,
  deleteProject,
  deleteProvider,
  fetchAgentEvents,
  fetchChat,
  fetchMetrics,
  fetchPlan,
  finishReview,
  getConfigScope,
  getDefaultAgent,
  getReasonerStatus,
  getRunnerStatus,
  listAgents,
  listCapabilities,
  listModels,
  listPlans,
  listProjects,
  listProviders,
  pausePlan,
  proposeIntent,
  renameModel,
  recordOutputDisposition,
  reopenReview,
  replanFromReview,
  replanMidRunning,
  resumePlan,
  sendDiscoveryMessage,
  sendReplanningMessage,
  setConfigKey,
  setDefaultAgent,
  subscribeToEvents,
  updateAgent,
  updateCapability,
  updateProject,
  updateProvider,
  type EditBody,
  type SSEEvent,
} from "./api";
import { toast, errorDetail } from "./toast";
import { usePlannerStore } from "../store/plannerStore";
import type {
  AgentBody,
  Capability,
  MessageResponse,
  Plan,
  PlanPhase,
  ProviderCreateBody,
  ProviderUpdateBody,
} from "../types/ui";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000, // SSE invalidation is the primary update path
      refetchOnWindowFocus: true,
      retry: 1,
    },
  },
});

// ─── Query keys ────────────────────────────────────────────────────────────────

export const keys = {
  plans: ["plans"] as const,
  plan: (id: string) => ["plan", id] as const,
  chat: (id: string) => ["chat", id] as const,
  agents: ["agents"] as const,
  defaultAgent: ["agents", "default"] as const,
  capabilities: ["capabilities"] as const,
  providers: ["providers"] as const,
  models: ["models"] as const,
  projects: ["projects"] as const,
  config: (scope: string) => ["config", scope] as const,
  reasonerStatus: ["reasoner-status"] as const,
  runnerStatus: ["runner-status"] as const,
  agentEvents: (planId: string, taskId?: string) =>
    ["agent-events", planId, taskId ?? "*"] as const,
  metrics: (planId?: string) => ["metrics", planId ?? "*"] as const,
};

// ─── Queries ───────────────────────────────────────────────────────────────────

export function usePlans() {
  return useQuery({ queryKey: keys.plans, queryFn: listPlans });
}

export function usePlan(planId: string | null) {
  return useQuery({
    queryKey: keys.plan(planId ?? ""),
    queryFn: () => fetchPlan(planId as string),
    enabled: !!planId,
  });
}

export function useChat(planId: string | null) {
  return useQuery({
    queryKey: keys.chat(planId ?? ""),
    queryFn: () => fetchChat(planId as string),
    enabled: !!planId,
  });
}

/** Durable agent/reasoner telemetry history for a plan (optionally one task). */
export function useAgentEvents(planId: string | null, taskId?: string) {
  return useQuery({
    queryKey: keys.agentEvents(planId ?? "", taskId),
    queryFn: () => fetchAgentEvents(planId as string, { taskId }),
    enabled: !!planId,
  });
}

/** Global (or per-plan) telemetry roll-up; polled while mounted. */
export function useMetrics(planId?: string) {
  return useQuery({
    queryKey: keys.metrics(planId),
    queryFn: () => fetchMetrics(planId),
    refetchInterval: 15000,
  });
}

export function useAgents() {
  return useQuery({ queryKey: keys.agents, queryFn: listAgents });
}

export function useCapabilities() {
  return useQuery({ queryKey: keys.capabilities, queryFn: listCapabilities });
}

export function useProviders() {
  return useQuery({ queryKey: keys.providers, queryFn: listProviders });
}

export function useModels() {
  return useQuery({ queryKey: keys.models, queryFn: listModels });
}

export function useProjects() {
  return useQuery({ queryKey: keys.projects, queryFn: listProjects });
}

export function useDefaultAgent() {
  return useQuery({ queryKey: keys.defaultAgent, queryFn: getDefaultAgent });
}

export function useConfigScope(scope: string) {
  return useQuery({
    queryKey: keys.config(scope),
    queryFn: () => getConfigScope(scope),
  });
}

export function useReasonerStatus() {
  return useQuery({
    queryKey: keys.reasonerStatus,
    queryFn: getReasonerStatus,
  });
}

export function useRunnerStatus() {
  return useQuery({ queryKey: keys.runnerStatus, queryFn: getRunnerStatus });
}

// ─── Mutations ─────────────────────────────────────────────────────────────────

export function useCreatePlan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ brief, projectId }: { brief: string; projectId: string }) =>
      createPlan(brief, projectId, nanoid()),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.plans }),
    onError: (err) => toast.error("Create plan failed", errorDetail(err)),
  });
}

/**
 * One conversation turn, routed by the plan's phase (DISCOVERY vs
 * REPLANNING — the only two chat-driven phases). The reply lands in the
 * chat cache; a committed turn also advances the phase, so the plan cache
 * refetches.
 */
export function useSendMessage(planId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (message: string): Promise<MessageResponse> => {
      const plan = qc.getQueryData<Plan>(keys.plan(planId));
      const send =
        plan?.phase === "replanning"
          ? sendReplanningMessage
          : sendDiscoveryMessage;
      return send(planId, message);
    },
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: keys.chat(planId) });
      if (result.committed) {
        qc.invalidateQueries({ queryKey: keys.plan(planId) });
        qc.invalidateQueries({ queryKey: keys.plans });
        toast.success("Roadmap committed", `Plan moved to ${result.phase}`);
      }
    },
    onError: (err) => {
      qc.invalidateQueries({ queryKey: keys.chat(planId) });
      toast.error("Message failed", errorDetail(err));
    },
  });
}

function usePlanCommand(
  planId: string,
  fn: (planId: string) => Promise<void>,
  label: string,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => fn(planId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.plan(planId) });
      qc.invalidateQueries({ queryKey: keys.plans });
    },
    onError: (err) => toast.error(`${label} failed`, errorDetail(err)),
  });
}

export const useApprovePlan = (planId: string) =>
  usePlanCommand(planId, approvePlan, "Approve");
export const useFinishReview = (planId: string) =>
  usePlanCommand(planId, finishReview, "Finish review");
export const useReplanFromReview = (planId: string) =>
  usePlanCommand(planId, replanFromReview, "Replan");
export const useReplanMidRunning = (planId: string) =>
  usePlanCommand(planId, replanMidRunning, "Replan");
export const useReopenReview = (planId: string) =>
  usePlanCommand(planId, reopenReview, "Request changes");
export const useResumePlan = (planId: string) =>
  usePlanCommand(planId, resumePlan, "Resume");

export function useStartIntent(
  planId: string,
  kind: "initial" | "replan",
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => {
      const plan = qc.getQueryData<Plan>(keys.plan(planId));
      if (!plan) throw new Error("Plan details are not loaded");
      return proposeIntent(planId, {
        objective: plan.brief,
        scope: [],
        constraints: [],
        exclusions: [],
        kind,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.plan(planId) });
      qc.invalidateQueries({ queryKey: keys.plans });
    },
    onError: (err) => toast.error("Start intent failed", errorDetail(err)),
  });
}

export const useApproveIntentGate = (
  planId: string,
  gateId: string,
  revision: number,
) =>
  usePlanCommand(
    planId,
    (id) => approveIntentGate(id, gateId, revision),
    "Approve intent",
  );

export const useCancelIntent = (planId: string) =>
  usePlanCommand(planId, cancelIntent, "Cancel intent");

export const useActivateCycle = (
  planId: string,
  gateId: string,
  revision: number,
) =>
  usePlanCommand(
    planId,
    async (id) => { await activateCycle(id, gateId, revision); },
    "Activate cycle",
  );

export const useCancelCycleDraft = (planId: string) =>
  usePlanCommand(planId, cancelCycleDraft, "Cancel cycle draft");

export function useRecordOutputDisposition(
  planId: string,
  gateId: string,
  revision: number,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      disposition,
      outputReference,
    }: {
      disposition: "open_pr" | "merge" | "retain_branch" | "discard";
      outputReference: string | null;
    }) => recordOutputDisposition(
      planId,
      gateId,
      revision,
      disposition,
      outputReference,
    ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.plan(planId) });
      qc.invalidateQueries({ queryKey: keys.plans });
    },
    onError: (err) => toast.error("Publication decision failed", errorDetail(err)),
  });
}

export function usePausePlan(planId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (reason?: string) => pausePlan(planId, reason),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.plan(planId) });
      qc.invalidateQueries({ queryKey: keys.plans });
    },
    onError: (err) => toast.error("Pause failed", errorDetail(err)),
  });
}

export function useApplyEdit(planId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (edit: EditBody) => applyEdit(planId, edit),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.plan(planId) }),
    onError: (err) => toast.error("Edit rejected", errorDetail(err)),
  });
}

// ─── Reference-data + config mutations ─────────────────────────────────────────

/** Shared shape: invalidate the affected keys on success, toast on error. */
function useRefMutation<TArgs, TResult>(
  fn: (args: TArgs) => Promise<TResult>,
  label: string,
  invalidates: readonly (readonly string[])[],
  onSuccessToast?: string,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      for (const key of invalidates) qc.invalidateQueries({ queryKey: key });
      if (onSuccessToast) toast.success(onSuccessToast);
    },
    onError: (err) => toast.error(`${label} failed`, errorDetail(err)),
  });
}

// Providers — deletes cascade models; both can rewire the reasoner status.
export const useCreateProvider = () =>
  useRefMutation(
    (body: ProviderCreateBody) => createProvider(body),
    "Create provider",
    [keys.providers, keys.reasonerStatus, keys.runnerStatus],
    "Provider created",
  );
export const useUpdateProvider = () =>
  useRefMutation(
    ({ id, body }: { id: string; body: ProviderUpdateBody }) =>
      updateProvider(id, body),
    "Update provider",
    [keys.providers, keys.reasonerStatus, keys.runnerStatus],
    "Provider saved",
  );
export const useDeleteProvider = () =>
  useRefMutation(
    (id: string) => deleteProvider(id),
    "Delete provider",
    [keys.providers, keys.models, keys.reasonerStatus, keys.runnerStatus],
    "Provider deleted",
  );

// Models
export const useCreateModel = () =>
  useRefMutation(
    ({ providerId, name }: { providerId: string; name: string }) =>
      createModel(providerId, name),
    "Add model",
    [keys.models, keys.providers, keys.reasonerStatus, keys.runnerStatus],
    "Model added",
  );
export const useRenameModel = () =>
  useRefMutation(
    ({ modelId, name }: { modelId: string; name: string }) =>
      renameModel(modelId, name),
    "Rename model",
    [keys.models, keys.providers, keys.reasonerStatus, keys.runnerStatus],
    "Model renamed",
  );
export const useDeleteModel = () =>
  useRefMutation(
    (modelId: string) => deleteModel(modelId),
    "Delete model",
    [keys.models, keys.providers, keys.reasonerStatus, keys.runnerStatus],
    "Model deleted",
  );

// Capabilities — agents embed capability objects, so refresh those too.
export const useCreateCapability = () =>
  useRefMutation(
    (cap: Capability) => createCapability(cap),
    "Create capability",
    [keys.capabilities, keys.agents],
    "Capability created",
  );
export const useUpdateCapability = () =>
  useRefMutation(
    ({ id, cap }: { id: string; cap: Capability }) => updateCapability(id, cap),
    "Update capability",
    [keys.capabilities, keys.agents],
    "Capability saved",
  );
export const useDeleteCapability = () =>
  useRefMutation(
    (id: string) => deleteCapability(id),
    "Delete capability",
    [keys.capabilities, keys.agents],
    "Capability deleted",
  );

// Agents
export const useCreateAgent = () =>
  useRefMutation(
    (body: AgentBody) => createAgent(body),
    "Create agent",
    [keys.agents, keys.defaultAgent, keys.runnerStatus],
    "Agent created",
  );
export const useUpdateAgent = () =>
  useRefMutation(
    ({ id, body }: { id: string; body: AgentBody }) => updateAgent(id, body),
    "Update agent",
    [keys.agents, keys.defaultAgent, keys.runnerStatus],
    "Agent saved",
  );
export const useDeleteAgent = () =>
  useRefMutation(
    (id: string) => deleteAgent(id),
    "Delete agent",
    [keys.agents, keys.defaultAgent, keys.runnerStatus],
    "Agent deleted",
  );
export const useSetDefaultAgent = () =>
  useRefMutation(
    (id: string) => setDefaultAgent(id),
    "Set default agent",
    [keys.agents, keys.defaultAgent, keys.runnerStatus],
    "Default agent set",
  );

// Projects
export const useCreateProject = () =>
  useRefMutation(
    (body: { name: string; repo_url?: string | null }) => createProject(body),
    "Create project",
    [keys.projects],
    "Project created",
  );
export const useUpdateProject = () =>
  useRefMutation(
    ({
      id,
      body,
    }: {
      id: string;
      body: { name: string; repo_url?: string | null };
    }) => updateProject(id, body),
    "Update project",
    [keys.projects],
    "Project saved",
  );
export const useDeleteProject = () =>
  useRefMutation(
    (id: string) => deleteProject(id),
    "Delete project",
    [keys.projects],
    "Project deleted",
  );

// Config — a reasoner.* write immediately re-validates the status banner.
export function useSetConfigKey(scope: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ key, value }: { key: string; value: string }) =>
      setConfigKey(scope, key, value),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.config(scope) });
      qc.invalidateQueries({ queryKey: keys.reasonerStatus });
      qc.invalidateQueries({ queryKey: keys.runnerStatus });
      toast.success("Config saved", "Restart the API/worker to apply.");
    },
    onError: (err) => toast.error("Config save failed", errorDetail(err)),
  });
}

// ─── SSE → cache bridge ────────────────────────────────────────────────────────

const TASK_EVENTS = new Set([
  "TaskStarted",
  "TaskCompleted",
  "TaskRequeued",
  "TaskFailedEvent",
  "TaskAbandoned",
  "GoalCompleted",
  "GoalFailedEvent",
]);

const STATE_EVENTS = new Set([
  "PauseRequested",
  "PlanBlocked",
  "BlockResolved",
  "IntentProposed",
  "IntentApproved",
  "CycleDrafted",
  "CycleVerified",
  "CycleActivated",
  "ReviewGateOpened",
  "OutputDispositionRecorded",
  "TestBundleFrozen",
  "TaskVerificationAccepted",
  "TaskVerificationRejected",
  "TaskRetried",
]);

/**
 * Subscribe to the backend event stream once (mount in the app shell).
 * Every event lands in the rolling buffer (Activity view); plan-scoped
 * events invalidate that plan's cache; agent.event feeds the console dock.
 * Delivery is at-least-once — the store dedups on event_id.
 */
export function useSSEBridge() {
  const qc = useQueryClient();
  const pushEvent = usePlannerStore((s) => s.pushEvent);
  const appendAgentLog = usePlannerStore((s) => s.appendAgentLog);
  const setConnectionState = usePlannerStore((s) => s.setConnectionState);

  useEffect(() => {
    const unsubscribe = subscribeToEvents({
      onOpen: () => setConnectionState("live"),
      onReconnecting: () => setConnectionState("reconnecting"),
      onDown: () => setConnectionState("down"),
      onReconnect: () => {
        // Events emitted during the gap are gone — resync everything.
        setConnectionState("live");
        qc.invalidateQueries();
      },
      onEvent: (event: SSEEvent) => {
        setConnectionState("live");
        const { payload } = event;
        const fresh = pushEvent(event.type, payload);
        if (!fresh) return; // at-least-once delivery: already seen event_id

        const planId = payload.plan_id;

        switch (event.type) {
          case "agent.event":
            appendAgentLog(payload);
            break;

          case "PhaseAdvanced": {
            qc.invalidateQueries({ queryKey: keys.plan(planId) });
            qc.invalidateQueries({ queryKey: keys.plans });
            const to = payload.to_phase as PlanPhase;
            if (to === "awaiting_review") {
              toast.info(
                "Plan ready for review",
                "Approve it to start execution.",
              );
            } else if (to === "review") {
              toast.info(
                "Execution finished",
                "Finish the plan or replan the next phase.",
              );
            }
            break;
          }

          case "PlanCompleted":
            toast.success("Plan completed");
            qc.invalidateQueries({ queryKey: keys.plan(planId) });
            qc.invalidateQueries({ queryKey: keys.plans });
            break;

          case "PlanFailed":
            toast.error("Plan failed", (payload.reason as string) ?? undefined);
            qc.invalidateQueries({ queryKey: keys.plan(planId) });
            qc.invalidateQueries({ queryKey: keys.plans });
            break;

          case "ReasonerFailed": {
            const reason = (payload.reason as string) ?? undefined;
            if (payload.transient) {
              // Backing off — the plan will retry on its own; a PlanFailed follows
              // only if the retry budget runs out.
              toast.info("Planner backing off", reason);
            } else {
              toast.error("Planner unavailable", reason);
            }
            qc.invalidateQueries({ queryKey: keys.plan(planId) });
            qc.invalidateQueries({ queryKey: keys.plans });
            break;
          }

          case "PlanPaused": {
            const reason = (payload.reason as string) ?? undefined;
            if (payload.auto) {
              // the system paused itself (a task exhausted its retries or failed
              // non-retryably) — it needs a human to edit and resume
              toast.error(
                "Plan needs attention",
                reason ?? "Paused after a failure.",
              );
            } else {
              toast.info(
                "Plan paused",
                "Goals and tasks are editable while paused.",
              );
            }
            qc.invalidateQueries({ queryKey: keys.plan(planId) });
            qc.invalidateQueries({ queryKey: keys.plans });
            break;
          }

          case "PlanResumed":
            toast.info("Plan resumed", "Failed tasks were requeued for retry.");
            qc.invalidateQueries({ queryKey: keys.plan(planId) });
            qc.invalidateQueries({ queryKey: keys.plans });
            break;

          case "ReplanRequested":
            toast.info(
              "Replan requested",
              "Pending work was skipped; chat is open.",
            );
            qc.invalidateQueries({ queryKey: keys.plan(planId) });
            break;

          case "TaskFailedEvent":
            toast.error(
              "Task failed",
              [payload.task_id, payload.reason].filter(Boolean).join(" — "),
            );
            qc.invalidateQueries({ queryKey: keys.plan(planId) });
            break;

          case "AgentFellBackToDefault":
            toast.info(
              "Task fell back to the default agent",
              `No agent covers: ${(payload.required_capabilities as string[])?.join(", ")}`,
            );
            qc.invalidateQueries({ queryKey: keys.plan(planId) });
            break;

          default:
            if (TASK_EVENTS.has(event.type) || STATE_EVENTS.has(event.type)) {
              qc.invalidateQueries({ queryKey: keys.plan(planId) });
              qc.invalidateQueries({ queryKey: keys.plans });
            }
            break;
        }
      },
    });
    return unsubscribe;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
