/**
 * src/store/plannerStore.ts
 *
 * All state derives from the backend. On mount:
 *   1. loadPlan() hydrates plan, goals, agents, history
 *   2. subscribeToEvents() patches node statuses as the backend pushes changes
 *
 * The store never holds "seed" data — it is the frontend mirror of backend state.
 */

import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import {
  addEdge,
  applyNodeChanges,
  applyEdgeChanges,
  type Node,
  type Edge,
  type NodeChange,
  type EdgeChange,
  type Connection,
} from '@xyflow/react';
import { nanoid } from 'nanoid';

import type {
  TaskNodeData,
  ChatMessage,
  AgentProps,
  GoalAggregate,
  ProjectPlan,
  TaskStatus,
  PlannerUIState,
  HistoryEntry,
} from '../types/domain';
import {
  fetchPlan, fetchGoals, fetchAgents, fetchPlanHistory,
  fetchGoalHistory, sendChatMessage, subscribeToEvents,
  approveBrief, approveArchitecture, approvePhase,
  type SSEEvent,
} from '../lib/api';
import { buildFlowFromGoals } from '../lib/layout';
import { AGENT_COLORS } from '../styles/tokens';

function ts() {
  return new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
}

function colorAgent(a: AgentProps): AgentProps {
  return { ...a, color: AGENT_COLORS[a.name] ?? '#64748b' };
}

// ─── State shape ──────────────────────────────────────────────────────────────

interface PlannerState {
  // Backend data
  plan: ProjectPlan | null;
  goals: GoalAggregate[];
  agentRegistry: AgentProps[];
  loaded: boolean;
  loadError: string | null;

  // React Flow — mixed: 'goalGroup' group nodes + 'taskNode' children
  nodes: Node[];
  edges: Edge[];

  // Chat
  messages: ChatMessage[];

  // UI
  ui: PlannerUIState;

  // SSE unsubscribe handle
  _unsubscribe: (() => void) | null;

  // ── Lifecycle ──────────────────────────────────────────────────────────────
  loadPlan: () => Promise<void>;
  refreshGoals: () => Promise<void>;
  subscribeSSE: () => void;
  unsubscribeSSE: () => void;

  // ── React Flow ─────────────────────────────────────────────────────────────
  onNodesChange: (changes: NodeChange[]) => void;
  onEdgesChange: (changes: EdgeChange[]) => void;
  onConnect: (connection: Connection) => void;

  // ── Selection ──────────────────────────────────────────────────────────────
  selectNode: (id: string | null) => void;

  // ── M2: Approvals ──────────────────────────────────────────────────────────
  doApproveBrief: () => Promise<void>;
  doApproveArchitecture: (decisionIds: string[]) => Promise<void>;
  doApprovePhase: (approveNext: boolean) => Promise<void>;

  // ── Chat ───────────────────────────────────────────────────────────────────
  sendMessage: (text: string) => Promise<void>;
  addMessage: (msg: Omit<ChatMessage, 'id'>) => void;
  setThinking: (v: boolean) => void;

  // ── Layout ─────────────────────────────────────────────────────────────────
  autoLayout: () => void;
  setLayoutDirection: (dir: 'LR' | 'TB') => void;

  // ── UI toggles ─────────────────────────────────────────────────────────────
  toggleChatPanel: () => void;
}

// ─── Store ────────────────────────────────────────────────────────────────────

export const usePlannerStore = create<PlannerState>()(
  immer((set, get) => ({
    plan: null,
    goals: [],
    agentRegistry: [],
    loaded: false,
    loadError: null,
    nodes: [],
    edges: [],
    messages: [],
    _unsubscribe: null,

    ui: {
      selectedNodeId: null,
      selectedGoalId: null,
      detailPanelOpen: false,
      chatPanelCollapsed: false,
      isThinking: false,
      layoutDirection: 'LR',
    },

    // ── Load everything from backend ──────────────────────────────────────────
    loadPlan: async () => {
      set((s) => { s.loaded = false; s.loadError = null; });
      try {
        const [plan, goals, agents, planHistory] = await Promise.all([
          fetchPlan(),
          fetchGoals(),
          fetchAgents(),
          fetchPlanHistory(),
        ]);

        const coloredAgents = agents.map(colorAgent);
        const { nodes, edges } = buildFlowFromGoals(goals, coloredAgents, get().ui.layoutDirection, plan);

        // Hydrate chat from backend history
        const historyMessages: ChatMessage[] = planHistory
          .slice(-20) // last 20 events as context
          .map((h) => ({
            id: nanoid(),
            role: 'system' as const,
            text: `[${h.actor}] ${h.event}${h.detail ? ' — ' + JSON.stringify(h.detail) : ''}`,
            ts: h.ts ? new Date(h.ts).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }) : ts(),
          }));

        const introMsg: ChatMessage = {
          id: nanoid(),
          role: 'assistant',
          text: `AIPOM connected. Plan status: ${plan.status} · ${goals.length} goals · ${goals.reduce((n, g) => n + g.tasks.length, 0)} tasks. ${
            plan.status === 'phase_active'
              ? 'Chat is wired to the planning engine — type a refinement request.'
              : plan.status === 'discovery'
                ? 'Discovery is active. Answer questions to build the project brief.'
                : 'Use the approval buttons in the toolbar to advance the plan.'
          }`,
          ts: ts(),
        };

        set((s) => {
          s.plan = plan;
          s.goals = goals;
          s.agentRegistry = coloredAgents;
          s.nodes = nodes;
          s.edges = edges;
          s.loaded = true;
          s.messages = [...historyMessages, introMsg];
        });

        get().subscribeSSE();
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        set((s) => {
          s.loadError = msg;
          s.loaded = true;
          s.messages = [{
            id: nanoid(), role: 'system',
            text: `Failed to connect to backend: ${msg}. Is the server running at ${import.meta.env.VITE_API_URL ?? 'http://localhost:8000'}?`,
            ts: ts(),
          }];
        });
      }
    },

    refreshGoals: async () => {
      try {
        const goals = await fetchGoals();
        const agents = get().agentRegistry;
        const { nodes, edges } = buildFlowFromGoals(goals, agents, get().ui.layoutDirection, get().plan);
        set((s) => {
          s.goals = goals;
          s.nodes = nodes;
          s.edges = edges;
        });
      } catch {
        // silent — SSE will catch individual updates
      }
    },

    // ── SSE ────────────────────────────────────────────────────────────────────
    subscribeSSE: () => {
      const existing = get()._unsubscribe;
      if (existing) existing();

      const unsub = subscribeToEvents(
        (event: SSEEvent) => {
          const now = ts();
          switch (event.type) {
            case 'plan.status_changed':
              set((s) => {
                if (s.plan) s.plan.status = event.payload.status as any;
              });
              get().addMessage({ role: 'system', text: `Plan status → ${event.payload.status}`, ts: now });
              break;

            case 'task.status_changed': {
              const { task_id, status } = event.payload as { task_id: string; status: TaskStatus };
              set((s) => {
                const node = s.nodes.find((n) => n.id === task_id);
                if (node?.type === 'taskNode') (node.data as TaskNodeData).task.status = status;
                // Also update goal task summaries
                for (const g of s.goals) {
                  const t = g.tasks.find((t) => t.task_id === task_id);
                  if (t) t.status = status;
                }
              });
              get().addMessage({ role: 'system', text: `${task_id} → ${status}`, ts: now });
              break;
            }

            case 'goal.dispatched':
              get().addMessage({
                role: 'system',
                text: `Goal dispatched: ${event.payload.goal_id}`,
                ts: now,
              });
              // Refresh to pick up new goal
              get().refreshGoals();
              break;

            case 'plan.refinement_action':
              get().addMessage({
                role: 'system',
                text: `↳ ${(event.payload as any).action}`,
                ts: now,
              });
              break;

            case 'plan.jit_progress':
              get().addMessage({
                role: 'system',
                text: `JIT planner: ${JSON.stringify(event.payload)}`,
                ts: now,
              });
              break;

            case 'plan.decision_proposed':
              get().addMessage({
                role: 'tool',
                toolName: 'propose_decision',
                text: `Decision proposed [${event.payload.domain}] — id: ${event.payload.id}. Review it when approving the architecture.`,
                ts: now,
              });
              break;

            case 'plan.phase_proposed':
              get().addMessage({
                role: 'tool',
                toolName: 'propose_phases',
                text: `Phase proposed: "${event.payload.name}" with goals: ${event.payload.goal_names.join(', ') || '(none)'}`,
                ts: now,
              });
              break;
          }
        },
        () => {
          // On SSE error, stop trying — show in chat
          get().addMessage({
            role: 'system',
            text: 'Live event stream disconnected. Canvas reflects last known state.',
            ts: ts(),
          });
        },
      );

      set((s) => { s._unsubscribe = unsub; });
    },

    unsubscribeSSE: () => {
      const unsub = get()._unsubscribe;
      if (unsub) { unsub(); set((s) => { s._unsubscribe = null; }); }
    },

    // ── React Flow handlers ───────────────────────────────────────────────────
    onNodesChange: (changes) => {
      set((s) => { s.nodes = applyNodeChanges(changes, s.nodes); });
    },
    onEdgesChange: (changes) => {
      set((s) => { s.edges = applyEdgeChanges(changes, s.edges); });
    },
    onConnect: (connection) => {
      set((s) => {
        s.edges = addEdge({ ...connection, id: `edge-${nanoid(6)}`, type: 'smoothstep' }, s.edges);
      });
      get().addMessage({ role: 'system', text: `Edge connected: ${connection.source} → ${connection.target}`, ts: ts() });
    },

    // ── Selection ─────────────────────────────────────────────────────────────
    selectNode: (id) => {
      set((s) => {
        s.ui.selectedNodeId = id;
        s.ui.detailPanelOpen = id !== null;
        // Find parent goal
        if (id) {
          const goal = s.goals.find((g) => g.tasks.some((t) => t.task_id === id));
          s.ui.selectedGoalId = goal?.goal_id ?? null;
        } else {
          s.ui.selectedGoalId = null;
        }
        s.nodes = s.nodes.map((n) =>
          n.type === 'taskNode' ? { ...n, data: { ...n.data, selected: n.id === id } } : n,
        );
      });
    },

    // ── M2: Approvals ──────────────────────────────────────────────────────────
    doApproveBrief: async () => {
      get().setThinking(true);
      try {
        const result = await approveBrief();
        get().addMessage({ role: 'system', text: `Brief approved → ${result.plan_status}`, ts: ts() });
        set((s) => { if (s.plan) s.plan.status = result.plan_status as any; });
      } catch (err) {
        get().addMessage({ role: 'system', text: `Approve brief failed: ${err}`, ts: ts() });
      } finally {
        get().setThinking(false);
      }
    },

    doApproveArchitecture: async (decisionIds) => {
      get().setThinking(true);
      try {
        const result = await approveArchitecture(decisionIds);
        get().addMessage({
          role: 'assistant',
          text: `Architecture approved. ${result.decisions_applied} decisions applied. ${result.goals_dispatched.length} goals dispatched.`,
          ts: ts(),
        });
        set((s) => { if (s.plan) s.plan.status = result.plan_status as any; });
        await get().refreshGoals();
      } catch (err) {
        get().addMessage({ role: 'system', text: `Approve architecture failed: ${err}`, ts: ts() });
      } finally {
        get().setThinking(false);
      }
    },

    doApprovePhase: async (approveNext) => {
      get().setThinking(true);
      try {
        const result = await approvePhase(approveNext);
        get().addMessage({
          role: 'assistant',
          text: `Phase approved → ${result.plan_status}. ${result.goals_dispatched.length} new goals dispatched.`,
          ts: ts(),
        });
        set((s) => { if (s.plan) s.plan.status = result.plan_status as any; });
        await get().refreshGoals();
      } catch (err) {
        get().addMessage({ role: 'system', text: `Approve phase failed: ${err}`, ts: ts() });
      } finally {
        get().setThinking(false);
      }
    },

    // ── Chat ──────────────────────────────────────────────────────────────────
    sendMessage: async (text) => {
      const { ui, plan, goals } = get();
      if (!text.trim() || ui.isThinking) return;
      const now = ts();

      get().addMessage({ role: 'user', text, ts: now, nodeCtx: ui.selectedNodeId ?? undefined });
      get().setThinking(true);

      try {
        const planStatus = plan?.status ?? 'discovery';
        const focusedGoalId = ui.selectedGoalId;

        const result = await sendChatMessage(text, planStatus, ui.selectedNodeId, focusedGoalId);

        switch (result.type) {
          case 'refinement': {
            const { data } = result;
            if (data.succeeded) {
              // actions_taken become system messages (already pushed via SSE),
              // but we show them here too as a summary
              get().addMessage({
                role: 'assistant',
                text: data.actions_taken.length > 0
                  ? `Done. Changes made:\n${data.actions_taken.map((a) => `• ${a}`).join('\n')}`
                  : 'No changes needed — the request was informational.',
                ts: now,
              });
              // Refresh canvas to reflect mutations
              await get().refreshGoals();
            } else {
              get().addMessage({
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
              get().addMessage({
                role: 'assistant',
                text: 'Discovery complete. Brief is ready for your approval. Use "Approve Brief" in the toolbar.',
                ts: now,
              });
            } else if (data.question) {
              get().addMessage({ role: 'assistant', text: data.question, ts: now });
            }
            break;
          }

          case 'advisory':
            get().addMessage({ role: 'assistant', text: result.text, ts: now });
            break;
        }
      } catch (err) {
        get().addMessage({
          role: 'assistant',
          text: `Error communicating with backend: ${err instanceof Error ? err.message : String(err)}`,
          ts: now,
        });
      } finally {
        get().setThinking(false);
      }
    },

    addMessage: (msg) => {
      set((s) => { s.messages.push({ id: nanoid(), ...msg }); });
    },

    setThinking: (v) => {
      set((s) => { s.ui.isThinking = v; });
    },

    // ── Layout ─────────────────────────────────────────────────────────────────
    autoLayout: () => {
      const { goals, agentRegistry, ui, plan } = get();
      const { nodes, edges } = buildFlowFromGoals(goals, agentRegistry, ui.layoutDirection, plan);
      set((s) => { s.nodes = nodes; s.edges = edges; });
    },

    setLayoutDirection: (dir) => {
      set((s) => { s.ui.layoutDirection = dir; });
      get().autoLayout();
    },

    // ── UI ─────────────────────────────────────────────────────────────────────
    toggleChatPanel: () => {
      set((s) => { s.ui.chatPanelCollapsed = !s.ui.chatPanelCollapsed; });
    },
  })),
);
