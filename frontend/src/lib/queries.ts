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

import { useEffect } from 'react';
import {
  QueryClient,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { nanoid } from 'nanoid';

import {
  applyEdit,
  approvePlan,
  createPlan,
  fetchChat,
  fetchPlan,
  finishReview,
  listAgents,
  listCapabilities,
  listPlans,
  replanFromReview,
  replanMidRunning,
  sendDiscoveryMessage,
  sendReplanningMessage,
  subscribeToEvents,
  type EditBody,
  type SSEEvent,
} from './api';
import { toast, errorDetail } from './toast';
import { usePlannerStore } from '../store/plannerStore';
import type { MessageResponse, Plan, PlanPhase } from '../types/ui';

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
  plans: ['plans'] as const,
  plan: (id: string) => ['plan', id] as const,
  chat: (id: string) => ['chat', id] as const,
  agents: ['agents'] as const,
  capabilities: ['capabilities'] as const,
};

// ─── Queries ───────────────────────────────────────────────────────────────────

export function usePlans() {
  return useQuery({ queryKey: keys.plans, queryFn: listPlans });
}

export function usePlan(planId: string | null) {
  return useQuery({
    queryKey: keys.plan(planId ?? ''),
    queryFn: () => fetchPlan(planId as string),
    enabled: !!planId,
  });
}

export function useChat(planId: string | null) {
  return useQuery({
    queryKey: keys.chat(planId ?? ''),
    queryFn: () => fetchChat(planId as string),
    enabled: !!planId,
  });
}

export function useAgents() {
  return useQuery({ queryKey: keys.agents, queryFn: listAgents });
}

export function useCapabilities() {
  return useQuery({ queryKey: keys.capabilities, queryFn: listCapabilities });
}

// ─── Mutations ─────────────────────────────────────────────────────────────────

export function useCreatePlan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (brief: string) => createPlan(brief, nanoid()),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.plans }),
    onError: (err) => toast.error('Create plan failed', errorDetail(err)),
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
        plan?.phase === 'replanning' ? sendReplanningMessage : sendDiscoveryMessage;
      return send(planId, message);
    },
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: keys.chat(planId) });
      if (result.committed) {
        qc.invalidateQueries({ queryKey: keys.plan(planId) });
        qc.invalidateQueries({ queryKey: keys.plans });
        toast.success('Roadmap committed', `Plan moved to ${result.phase}`);
      }
    },
    onError: (err) => {
      qc.invalidateQueries({ queryKey: keys.chat(planId) });
      toast.error('Message failed', errorDetail(err));
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
  usePlanCommand(planId, approvePlan, 'Approve');
export const useFinishReview = (planId: string) =>
  usePlanCommand(planId, finishReview, 'Finish review');
export const useReplanFromReview = (planId: string) =>
  usePlanCommand(planId, replanFromReview, 'Replan');
export const useReplanMidRunning = (planId: string) =>
  usePlanCommand(planId, replanMidRunning, 'Replan');

export function useApplyEdit(planId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (edit: EditBody) => applyEdit(planId, edit),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.plan(planId) }),
    onError: (err) => toast.error('Edit rejected', errorDetail(err)),
  });
}

// ─── SSE → cache bridge ────────────────────────────────────────────────────────

const TASK_EVENTS = new Set([
  'TaskStarted',
  'TaskCompleted',
  'TaskRequeued',
  'TaskFailedEvent',
  'TaskAbandoned',
  'GoalCompleted',
  'GoalFailedEvent',
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
      onOpen: () => setConnectionState('live'),
      onReconnecting: () => setConnectionState('reconnecting'),
      onDown: () => setConnectionState('down'),
      onReconnect: () => {
        // Events emitted during the gap are gone — resync everything.
        setConnectionState('live');
        qc.invalidateQueries();
      },
      onEvent: (event: SSEEvent) => {
        setConnectionState('live');
        const { payload } = event;
        const fresh = pushEvent(event.type, payload);
        if (!fresh) return; // at-least-once delivery: already seen event_id

        const planId = payload.plan_id;

        switch (event.type) {
          case 'agent.event':
            appendAgentLog(payload);
            break;

          case 'PhaseAdvanced': {
            qc.invalidateQueries({ queryKey: keys.plan(planId) });
            qc.invalidateQueries({ queryKey: keys.plans });
            const to = payload.to_phase as PlanPhase;
            if (to === 'awaiting_review') {
              toast.info('Plan ready for review', 'Approve it to start execution.');
            } else if (to === 'review') {
              toast.info('Execution finished', 'Finish the plan or replan the next phase.');
            }
            break;
          }

          case 'PlanCompleted':
            toast.success('Plan completed');
            qc.invalidateQueries({ queryKey: keys.plan(planId) });
            qc.invalidateQueries({ queryKey: keys.plans });
            break;

          case 'PlanFailed':
            toast.error('Plan failed', (payload.reason as string) ?? undefined);
            qc.invalidateQueries({ queryKey: keys.plan(planId) });
            qc.invalidateQueries({ queryKey: keys.plans });
            break;

          case 'ReplanRequested':
            toast.info('Replan requested', 'Pending work was skipped; chat is open.');
            qc.invalidateQueries({ queryKey: keys.plan(planId) });
            break;

          case 'TaskFailedEvent':
            toast.error(
              'Task failed',
              [payload.task_id, payload.reason].filter(Boolean).join(' — '),
            );
            qc.invalidateQueries({ queryKey: keys.plan(planId) });
            break;

          case 'AgentFellBackToDefault':
            toast.info(
              'Task fell back to the default agent',
              `No agent covers: ${(payload.required_capabilities as string[])?.join(', ')}`,
            );
            qc.invalidateQueries({ queryKey: keys.plan(planId) });
            break;

          default:
            if (TASK_EVENTS.has(event.type)) {
              qc.invalidateQueries({ queryKey: keys.plan(planId) });
            }
            break;
        }
      },
    });
    return unsubscribe;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
