/**
 * src/lib/api.ts
 *
 * All HTTP + SSE against the thin orchestrator API. Plan-scoped endpoints
 * (the 9-phase lifecycle), the two chat endpoints (DISCOVERY / REPLANNING),
 * reference-data CRUD, two-tier config, and the /api/events stream.
 *
 * Errors carry the server's enveloped body text so the toast layer can
 * surface code/message/request_id.
 */

import type {
  AgentBody,
  AgentSpec,
  Capability,
  ChatMessageResponse,
  DefaultAgentResponse,
  IaModel,
  MessageResponse,
  ModelProvider,
  Plan,
  PlanSummary,
  ProjectDefinition,
  ProviderCreateBody,
  ProviderUpdateBody,
  ReasonerStatusResponse,
  RunnerStatusResponse,
  SSEPayload,
} from "../types/ui";

const BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// Control-plane auth (reference/config routers): open when the backend has no
// ORCHESTRATOR_API_TOKEN; otherwise mirror it here as VITE_API_TOKEN.
const API_TOKEN = import.meta.env.VITE_API_TOKEN as string | undefined;

// ─── Helpers ──────────────────────────────────────────────────────────────────

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  headers?: Record<string, string>,
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: {
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(API_TOKEN ? { "X-API-Token": API_TOKEN } : {}),
      ...headers,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${method} ${path} → ${res.status}: ${text}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

const get = <T>(path: string) => request<T>("GET", path);
const post = <T>(
  path: string,
  body?: unknown,
  headers?: Record<string, string>,
) => request<T>("POST", path, body, headers);
const put = <T>(path: string, body?: unknown) => request<T>("PUT", path, body);
const del = <T>(path: string) => request<T>("DELETE", path);
const enc = encodeURIComponent;

// ─── Plans: lifecycle ─────────────────────────────────────────────────────────

export const listPlans = (): Promise<PlanSummary[]> => get("/api/plans");

export const fetchPlan = (planId: string): Promise<Plan> =>
  get(`/api/plans/${encodeURIComponent(planId)}`);

export const createPlan = (
  brief: string,
  projectId: string,
  idempotencyKey: string,
): Promise<{ plan_id: string }> =>
  post(
    "/api/plans",
    { brief, project_id: projectId },
    { "Idempotency-Key": idempotencyKey },
  );

/** Human approval at the pre-execution gate: AWAITING_REVIEW -> RUNNING. */
export const approvePlan = (planId: string): Promise<void> =>
  post(`/api/plans/${encodeURIComponent(planId)}/approve`);

/** Human "finish" at the post-execution gate: REVIEW -> DONE. */
export const finishReview = (planId: string): Promise<void> =>
  post(`/api/plans/${encodeURIComponent(planId)}/review/finish`);

/** Human "replan next phase" at the post-execution gate: REVIEW -> REPLANNING. */
export const replanFromReview = (planId: string): Promise<void> =>
  post(`/api/plans/${encodeURIComponent(planId)}/review/replan`);

/** Mid-RUNNING replan: skip pending work -> REPLANNING. */
export const replanMidRunning = (planId: string): Promise<void> =>
  post(`/api/plans/${encodeURIComponent(planId)}/replan`);

/** Request changes at the pre-execution gate: AWAITING_REVIEW -> DISCOVERY. */
export const reopenReview = (planId: string): Promise<void> =>
  post(`/api/plans/${encodeURIComponent(planId)}/review/reopen`);

/** Arm the pause gate: the worker stops claiming; goals/tasks become editable. */
export const pausePlan = (planId: string, reason?: string): Promise<void> =>
  post(`/api/plans/${encodeURIComponent(planId)}/pause`, {
    reason: reason ?? null,
  });

/** Clear the pause gate and requeue failed work (the manual retry). */
export const resumePlan = (planId: string): Promise<void> =>
  post(`/api/plans/${encodeURIComponent(planId)}/resume`);

export interface IntentProposalBody {
  objective: string;
  scope: string[];
  constraints: string[];
  exclusions: string[];
  kind: "initial" | "replan";
  planner_session_ref?: string | null;
}

export const proposeIntent = (
  planId: string,
  body: IntentProposalBody,
): Promise<Record<string, unknown>> =>
  post(`/api/plans/${enc(planId)}/intent`, body);

export const approveIntentGate = (
  planId: string,
  gateId: string,
  subjectRevision: number,
): Promise<void> =>
  post(`/api/plans/${enc(planId)}/intent/approve`, {
    gate_id: gateId,
    subject_revision: subjectRevision,
  });

export const cancelIntent = (planId: string): Promise<void> =>
  del(`/api/plans/${enc(planId)}/intent`);

export const activateCycle = (
  planId: string,
  gateId: string,
  subjectRevision: number,
): Promise<Record<string, unknown>> =>
  post(`/api/plans/${enc(planId)}/cycle-draft/approve`, {
    gate_id: gateId,
    subject_revision: subjectRevision,
  });

export const cancelCycleDraft = (planId: string): Promise<void> =>
  del(`/api/plans/${enc(planId)}/cycle-draft`);

export const recordOutputDisposition = (
  planId: string,
  gateId: string,
  subjectRevision: number,
  disposition: "open_pr" | "merge" | "retain_branch" | "discard",
  outputReference: string | null,
): Promise<void> =>
  post(`/api/plans/${enc(planId)}/publication`, {
    gate_id: gateId,
    subject_revision: subjectRevision,
    disposition,
    output_reference: outputReference,
  });

// ─── Plans: conversation (multi-turn with commit) ─────────────────────────────

export const sendDiscoveryMessage = (
  planId: string,
  message: string,
): Promise<MessageResponse> =>
  post(`/api/plans/${encodeURIComponent(planId)}/discovery/message`, {
    message,
  });

export const sendReplanningMessage = (
  planId: string,
  message: string,
): Promise<MessageResponse> =>
  post(`/api/plans/${encodeURIComponent(planId)}/replanning/message`, {
    message,
  });

export const fetchChat = (planId: string): Promise<ChatMessageResponse[]> =>
  get(`/api/plans/${encodeURIComponent(planId)}/chat`);

// ─── Plans: structural edits ──────────────────────────────────────────────────

export interface EditBody {
  type:
    | "add_task"
    | "remove_task"
    | "reorder_tasks"
    | "edit_task_requirements"
    | "rebind_task_agent"
    | "update_task"
    | "update_goal"
    | "remove_goal";
  goal_id: string;
  task_id?: string;
  task?: {
    name: string;
    description?: string;
    required_capabilities?: string[];
  };
  ordered_task_ids?: string[];
  required_capabilities?: string[];
  agent_id?: string;
  name?: string;
  description?: string;
  depends_on?: string[];
}

export const applyEdit = (planId: string, edit: EditBody): Promise<void> =>
  post(`/api/plans/${encodeURIComponent(planId)}/edits`, edit);

// ─── Plans: telemetry read side ───────────────────────────────────────────────

export interface AgentEventRow {
  id: number;
  event_id: string;
  plan_id: string;
  task_id: string | null;
  attempt: number;
  seq: number;
  type: string;
  payload: Record<string, string>;
  occurred_at: string;
}

/** A plan's fine-grained agent/reasoner telemetry history (most-recent first). */
export const fetchAgentEvents = (
  planId: string,
  opts?: { taskId?: string; limit?: number },
): Promise<AgentEventRow[]> => {
  const params = new URLSearchParams();
  if (opts?.taskId) params.set("task_id", opts.taskId);
  if (opts?.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  return get(`/api/plans/${enc(planId)}/agent-events${qs ? `?${qs}` : ""}`);
};

export interface MetricsResponse {
  llm: {
    sessions: number;
    calls: number;
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
  agent: {
    runs: number;
    finished: number;
    failed: number;
    failures_by_kind: Record<string, number>;
  };
}

/** Global (or per-plan) telemetry roll-up: LLM tokens + agent run/failure counts. */
export const fetchMetrics = (planId?: string): Promise<MetricsResponse> =>
  get(`/api/metrics${planId ? `?plan_id=${enc(planId)}` : ""}`);

// ─── Reference data: reads ────────────────────────────────────────────────────

export const listAgents = (): Promise<AgentSpec[]> => get("/api/agents");
export const listCapabilities = (): Promise<Capability[]> =>
  get("/api/capabilities");
export const listProviders = (): Promise<ModelProvider[]> =>
  get("/api/providers");
export const listModels = (): Promise<IaModel[]> => get("/api/models");
export const listProjects = (): Promise<ProjectDefinition[]> =>
  get("/api/projects");
export const getDefaultAgent = (): Promise<DefaultAgentResponse> =>
  get("/api/agents/default");

// ─── Reference data: capabilities ─────────────────────────────────────────────

export const createCapability = (cap: Capability): Promise<Capability> =>
  post("/api/capabilities", cap);
export const updateCapability = (id: string, cap: Capability): Promise<void> =>
  put(`/api/capabilities/${enc(id)}`, cap);
export const deleteCapability = (id: string): Promise<void> =>
  del(`/api/capabilities/${enc(id)}`);

// ─── Reference data: agents ───────────────────────────────────────────────────

export const createAgent = (body: AgentBody): Promise<AgentSpec> =>
  post("/api/agents", body);
export const updateAgent = (id: string, body: AgentBody): Promise<void> =>
  put(`/api/agents/${enc(id)}`, body);
export const deleteAgent = (id: string): Promise<void> =>
  del(`/api/agents/${enc(id)}`);
export const setDefaultAgent = (id: string): Promise<void> =>
  post(`/api/agents/${enc(id)}/default`);

// ─── Reference data: providers & models ───────────────────────────────────────
// A provider API key travels ONCE in the create/update body; the backend
// stores it envelope-encrypted and only ever returns the api_key_ref URI.

export const createProvider = (
  body: ProviderCreateBody,
): Promise<ModelProvider> => post("/api/providers", body);
export const updateProvider = (
  id: string,
  body: ProviderUpdateBody,
): Promise<void> => put(`/api/providers/${enc(id)}`, body);
export const deleteProvider = (id: string): Promise<void> =>
  del(`/api/providers/${enc(id)}`);

export const createModel = (
  providerId: string,
  name: string,
): Promise<IaModel> =>
  post(`/api/providers/${enc(providerId)}/models`, { name });
export const renameModel = (modelId: string, name: string): Promise<void> =>
  put(`/api/models/${enc(modelId)}`, { name });
export const deleteModel = (modelId: string): Promise<void> =>
  del(`/api/models/${enc(modelId)}`);

// ─── Reference data: projects ─────────────────────────────────────────────────

export const createProject = (body: {
  name: string;
  repo_url?: string | null;
}): Promise<ProjectDefinition> => post("/api/projects", body);
export const updateProject = (
  id: string,
  body: { name: string; repo_url?: string | null },
): Promise<void> => put(`/api/projects/${enc(id)}`, body);
export const deleteProject = (id: string): Promise<void> =>
  del(`/api/projects/${enc(id)}`);

// ─── Two-tier config + reasoner status ────────────────────────────────────────

export const getConfigScope = (
  scope: string,
): Promise<Record<string, string>> => get(`/api/config/${enc(scope)}`);

export const setConfigKey = (
  scope: string,
  key: string,
  value: string,
): Promise<void> => put(`/api/config/${enc(scope)}/${enc(key)}`, { value });

export const deleteConfigKey = (scope: string, key: string): Promise<void> =>
  del(`/api/config/${enc(scope)}/${enc(key)}`);

/** Live catalog-wiring check of the stored reasoner.* config (always 200). */
export const getReasonerStatus = (): Promise<ReasonerStatusResponse> =>
  get("/api/reasoner/status");

/**
 * Agent-runner status (always 200): global mode, per-agent runtime bindings
 * against the catalog, and the CLI binary probes.
 */
export const getRunnerStatus = (): Promise<RunnerStatusResponse> =>
  get("/api/runner/status");

// ─── SSE subscription ─────────────────────────────────────────────────────────

/** The outbox event vocabulary + the agent telemetry feed. */
export const SSE_EVENT_TYPES = [
  "PhaseAdvanced",
  "TaskStarted",
  "TaskCompleted",
  "TaskRequeued",
  "TaskFailedEvent",
  "TaskAbandoned",
  "ReplanRequested",
  "GoalCompleted",
  "GoalFailedEvent",
  "PlanCompleted",
  "PlanFailed",
  "PlanPaused",
  "PlanResumed",
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
  "ReasonerFailed",
  "AgentFellBackToDefault",
  "agent.event",
] as const;

export type SSEEventType = (typeof SSE_EVENT_TYPES)[number];

export interface SSEEvent {
  type: SSEEventType;
  payload: SSEPayload;
}

export interface SubscribeCallbacks {
  onEvent: (event: SSEEvent) => void;
  /** Stream open for the first time. */
  onOpen?: () => void;
  /** Stream re-opened after a gap — events emitted meanwhile are gone; resync. */
  onReconnect?: () => void;
  /** EventSource is retrying automatically. */
  onReconnecting?: () => void;
  /** EventSource closed hard; a new one will be created after RETRY_MS. */
  onDown?: () => void;
}

const RETRY_MS = 3000;

/**
 * The backend emits NAMED SSE events (`event: <type>`), so a listener is
 * registered per known type — `onmessage` alone would never fire. Delivery is
 * at-least-once: consumers dedup on payload.event_id.
 */
export function subscribeToEvents(cb: SubscribeCallbacks): () => void {
  let es: EventSource | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let hadError = false;
  let closed = false;

  function connect() {
    es = new EventSource(`${BASE}/api/events`);

    es.onopen = () => {
      if (hadError) {
        hadError = false;
        cb.onReconnect?.();
      } else {
        cb.onOpen?.();
      }
    };

    for (const type of SSE_EVENT_TYPES) {
      es.addEventListener(type, (e: MessageEvent) => {
        try {
          cb.onEvent({ type, payload: JSON.parse(e.data) as SSEPayload });
        } catch {
          // ignore malformed events
        }
      });
    }

    es.onerror = () => {
      hadError = true;
      if (es?.readyState === EventSource.CLOSED) {
        cb.onDown?.();
        es?.close();
        if (!closed) retryTimer = setTimeout(connect, RETRY_MS);
      } else {
        cb.onReconnecting?.();
      }
    };
  }

  connect();

  return () => {
    closed = true;
    if (retryTimer) clearTimeout(retryTimer);
    es?.close();
  };
}
