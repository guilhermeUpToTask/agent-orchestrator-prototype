/**
 * src/lib/queries.ts
 *
 * React Query layer: all server state (plan, goals, agents, history) is
 * fetched and cached here. Zustand (plannerStore) holds only local UI
 * state — selection, layout direction, chat panel, chat transcript.
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

import {
  approveArchitecture,
  approveBrief,
  approvePhase,
  fetchAgents,
  fetchGoals,
  fetchPlan,
  fetchPlanHistory,
  runArchitecture,
  runPhaseReview,
  sendChatMessage,
  startDiscovery,
  subscribeToEvents,
  type SSEEvent,
} from './api';
import { toast, errorDetail } from './toast';
import { usePlannerStore, ts } from '../store/plannerStore';
import { AGENT_COLORS } from '../styles/tokens';
import type { AgentProps, GoalAggregate, ProjectPlan, ProjectPlanStatus, TaskStatus } from '../types/ui';

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,        // SSE invalidation is the primary update path
      refetchOnWindowFocus: true,
      retry: 1,
    },
  },
});

// ─── Query keys ────────────────────────────────────────────────────────────────

export const keys = {
  plan: ['plan'] as const,
  planHistory: ['plan', 'history'] as const,
  goals: ['goals'] as const,
  agents: ['agents'] as const,
};

// ─── Queries ───────────────────────────────────────────────────────────────────

export function usePlan() {
  return useQuery({ queryKey: keys.plan, queryFn: fetchPlan });
}

export function usePlanHistory() {
  return useQuery({ queryKey: keys.planHistory, queryFn: fetchPlanHistory });
}

export function useGoals() {
  return useQuery({ queryKey: keys.goals, queryFn: fetchGoals });
}

// Module-level so the function reference is stable — React Query only
// re-runs select (and returns a new array) when the underlying data changes.
const colorizeAgents = (agents: AgentProps[]): AgentProps[] =>
  agents.map((a) => ({ ...a, color: AGENT_COLORS[a.name] ?? '#64748b' }));

export function useAgents() {
  return useQuery({
    queryKey: keys.agents,
    queryFn: fetchAgents,
    select: colorizeAgents,
  });
}

// ─── Mutations ─────────────────────────────────────────────────────────────────

export function useApproveBrief() {
  const qc = useQueryClient();
  const addMessage = usePlannerStore((s) => s.addMessage);
  return useMutation({
    mutationFn: approveBrief,
    onSuccess: (result) => {
      addMessage({ role: 'system', text: `Brief approved → ${result.plan_status}`, ts: ts() });
      qc.invalidateQueries({ queryKey: keys.plan });
    },
    onError: (err) => {
      toast.error('Approve brief failed', errorDetail(err));
    },
  });
}

export function useApproveArchitecture() {
  const qc = useQueryClient();
  const addMessage = usePlannerStore((s) => s.addMessage);
  return useMutation({
    mutationFn: (decisionIds: string[]) => approveArchitecture(decisionIds),
    onSuccess: (result) => {
      addMessage({
        role: 'assistant',
        text: `Architecture approved. ${result.decisions_applied} decisions applied. ${result.goals_dispatched.length} goals dispatched.`,
        ts: ts(),
      });
      qc.invalidateQueries({ queryKey: keys.plan });
      qc.invalidateQueries({ queryKey: keys.goals });
    },
    onError: (err) => {
      toast.error('Approve architecture failed', errorDetail(err));
    },
  });
}

export function useApprovePhase() {
  const qc = useQueryClient();
  const addMessage = usePlannerStore((s) => s.addMessage);
  return useMutation({
    mutationFn: (approveNext: boolean) => approvePhase(approveNext),
    onSuccess: (result) => {
      addMessage({
        role: 'assistant',
        text: `Phase approved → ${result.plan_status}. ${result.goals_dispatched.length} new goals dispatched.`,
        ts: ts(),
      });
      qc.invalidateQueries({ queryKey: keys.plan });
      qc.invalidateQueries({ queryKey: keys.goals });
    },
    onError: (err) => {
      toast.error('Approve phase failed', errorDetail(err));
    },
  });
}

export function useStartDiscovery() {
  const addMessage = usePlannerStore((s) => s.addMessage);
  const setThinking = usePlannerStore((s) => s.setThinking);
  return useMutation({
    mutationFn: startDiscovery,
    onMutate: () => setThinking(true),
    onSettled: () => setThinking(false),
    onSuccess: (result) => {
      if (result.question) {
        addMessage({ role: 'assistant', text: result.question, ts: ts() });
      } else if (result.done) {
        addMessage({ role: 'assistant', text: 'Discovery complete. Brief ready for approval.', ts: ts() });
      }
    },
    onError: (err) => {
      toast.error('Start discovery failed', errorDetail(err));
    },
  });
}

/**
 * Kick off the autonomous architecture planner (202). Decisions then stream
 * in over SSE and the rail surfaces the approval gate. This closes the loop
 * that previously dead-ended at approve-architecture's 409.
 */
export function useRunArchitecture() {
  const setActiveRun = usePlannerStore((s) => s.setActiveRun);
  return useMutation({
    mutationFn: runArchitecture,
    onMutate: () => setActiveRun('architecture'),
    onError: (err) => {
      setActiveRun(null);
      toast.error('Could not start architecture drafting', errorDetail(err));
    },
  });
}

/** Kick off the autonomous phase-review planner (202; SSE-driven). */
export function useRunPhaseReview() {
  const setActiveRun = usePlannerStore((s) => s.setActiveRun);
  return useMutation({
    mutationFn: runPhaseReview,
    onMutate: () => setActiveRun('phase_review'),
    onError: (err) => {
      setActiveRun(null);
      toast.error('Could not start phase review', errorDetail(err));
    },
  });
}

/**
 * Chat send routed by the current plan status (read from the plan cache).
 * Refinement responses invalidate the goals cache so the canvas reflects
 * any plan mutations the tactical planner made.
 */
export function useSendChatMessage() {
  const qc = useQueryClient();
  const addMessage = usePlannerStore((s) => s.addMessage);
  const setThinking = usePlannerStore((s) => s.setThinking);
  const selectedNodeId = usePlannerStore((s) => s.ui.selectedNodeId);
  const isThinking = usePlannerStore((s) => s.ui.isThinking);
  const { data: goals } = useGoals();

  return async (text: string) => {
    if (!text.trim() || isThinking) return;
    const now = ts();
    const planStatus: ProjectPlanStatus =
      qc.getQueryData<ProjectPlan>(keys.plan)?.status ?? 'discovery';
    const focusedGoalId =
      (selectedNodeId
        ? goals?.find((g) => g.tasks.some((t) => t.task_id === selectedNodeId))?.goal_id
        : null) ?? null;

    addMessage({ role: 'user', text, ts: now, nodeCtx: selectedNodeId ?? undefined });
    setThinking(true);

    try {
      const result = await sendChatMessage(text, planStatus, selectedNodeId, focusedGoalId);
      switch (result.type) {
        case 'refinement': {
          const { data } = result;
          if (data.succeeded) {
            addMessage({
              role: 'assistant',
              text: data.actions_taken.length > 0
                ? `Done. Changes made:\n${data.actions_taken.map((a) => `• ${a}`).join('\n')}`
                : 'No changes needed — the request was informational.',
              ts: now,
            });
            qc.invalidateQueries({ queryKey: keys.goals });
          } else {
            addMessage({
              role: 'assistant',
              text: `Could not complete: ${data.error ?? 'Unknown error'}`,
              ts: now,
            });
          }
          break;
        }
        case 'discovery': {
          const { data } = result;
          if (data.done) {
            addMessage({
              role: 'assistant',
              text: 'Discovery complete. Brief is ready for your approval. Use "Approve Brief" in the toolbar.',
              ts: now,
            });
          } else if (data.question) {
            addMessage({ role: 'assistant', text: data.question, ts: now });
          }
          break;
        }
        case 'advisory':
          addMessage({ role: 'assistant', text: result.text, ts: now });
          break;
      }
    } catch (err) {
      toast.error('Error communicating with backend', errorDetail(err));
    } finally {
      setThinking(false);
    }
  };
}

// ─── SSE → cache bridge ────────────────────────────────────────────────────────

/**
 * Subscribe to the backend event stream once. Every event lands in the
 * rolling event buffer (Activity view) and patches/invalidates the right
 * caches. The chat transcript receives only operator-relevant planner
 * output (proposals) — system noise no longer floods the conversation.
 * Connection lifecycle drives the top-bar indicator. Mount once in App.
 */
export function useSSEBridge() {
  const qc = useQueryClient();
  const addMessage = usePlannerStore((s) => s.addMessage);
  const pushEvent = usePlannerStore((s) => s.pushEvent);
  const setConnectionState = usePlannerStore((s) => s.setConnectionState);
  const addDecision = usePlannerStore((s) => s.addDecision);
  const clearDecisions = usePlannerStore((s) => s.clearDecisions);
  const addPhase = usePlannerStore((s) => s.addPhase);
  const clearPhases = usePlannerStore((s) => s.clearPhases);
  const markRunComplete = usePlannerStore((s) => s.markRunComplete);
  const setActiveRun = usePlannerStore((s) => s.setActiveRun);
  const resetRuns = usePlannerStore((s) => s.resetRuns);

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
        pushEvent(event.type, (event as { payload?: Record<string, unknown> }).payload ?? {});

        switch (event.type) {
          case 'plan.status_changed': {
            const status = event.payload.status as ProjectPlanStatus;
            qc.setQueryData<ProjectPlan>(keys.plan, (plan) =>
              plan ? { ...plan, status } : plan,
            );
            qc.invalidateQueries({ queryKey: keys.plan });
            // Decisions and run flags belong to one phase pass — drop them
            // once the phase activates or the project ends.
            if (status === 'phase_active' || status === 'done') {
              clearDecisions();
              clearPhases();
              resetRuns();
            }
            break;
          }

          case 'plan.architecture_completed':
            markRunComplete('architecture');
            break;

          case 'plan.architecture_failed':
            setActiveRun(null);
            toast.error(
              'Architecture drafting failed',
              (event.payload?.error as string) ??
                'The planner run ended without producing decisions. Check the model supports tool use, then retry.',
            );
            break;

          case 'plan.phase_review_completed':
            markRunComplete('phase_review');
            break;

          case 'plan.phase_review_failed':
            setActiveRun(null);
            toast.error(
              'Phase review failed',
              (event.payload?.error as string) ??
                'The phase-review run ended unexpectedly. You can retry it from the rail.',
            );
            break;

          case 'task.status_changed': {
            const { task_id, status } = event.payload;
            // Patch the cache in place for instant feedback, then revalidate.
            qc.setQueryData<GoalAggregate[]>(keys.goals, (goals) =>
              goals?.map((g) => ({
                ...g,
                tasks: g.tasks.map((t) =>
                  t.task_id === task_id ? { ...t, status: status as TaskStatus } : t,
                ),
              })),
            );
            qc.invalidateQueries({ queryKey: keys.goals });
            break;
          }

          case 'goal.dispatched':
          case 'goal.pr_opened':
          case 'goal.pr_state_synced':
          case 'goal.finalized':
            qc.invalidateQueries({ queryKey: keys.goals });
            break;

          case 'plan.refinement_action':
            addMessage({ role: 'system', text: `↳ ${event.payload.action}`, ts: ts() });
            break;

          case 'plan.jit_progress':
            // Streamed planner progress — rendered by the rail session card
            // straight from the event buffer.
            break;

          case 'plan.decision_proposed':
            addDecision({ id: event.payload.id, domain: event.payload.domain });
            addMessage({
              role: 'tool',
              toolName: 'propose_decision',
              text: `Decision proposed [${event.payload.domain}] — review it in the architecture gate.`,
              ts: ts(),
            });
            break;

          case 'plan.phase_proposed': {
            const descriptions = event.payload.goal_descriptions ?? {};
            addPhase({
              name: event.payload.name,
              goal_names: event.payload.goal_names,
              goals: event.payload.goal_names.map((name) => ({
                name,
                description: descriptions[name] ?? '',
              })),
            });
            addMessage({
              role: 'tool',
              toolName: 'propose_phases',
              text: `Phase proposed: "${event.payload.name}" with goals: ${event.payload.goal_names.join(', ') || '(none)'}`,
              ts: ts(),
            });
            break;
          }

          default:
            // Unknown event forwarded by the backend bridge — refetch
            // conservatively rather than missing a state change.
            qc.invalidateQueries({ queryKey: keys.plan });
            qc.invalidateQueries({ queryKey: keys.goals });
            break;
        }
      },
    });
    return unsubscribe;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
