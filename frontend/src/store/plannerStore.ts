/**
 * src/store/plannerStore.ts
 *
 * Local UI state ONLY. All server state (plan, goals, agents, history)
 * lives in React Query (src/lib/queries.ts) and is kept fresh by SSE
 * cache invalidation. This store holds what the server doesn't know:
 * selection, panel visibility, the chat transcript, the SSE connection
 * state, the rolling event buffer, and decision proposals captured from
 * the stream (no REST endpoint exposes them).
 */

import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import { nanoid } from 'nanoid';

import type { ChatMessage, PlannerUIState } from '../types/ui';

export function ts() {
  return new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
}

// ─── Live-stream domain types ───────────────────────────────────────────────

export type ConnectionState = 'connecting' | 'live' | 'reconnecting' | 'down';

export interface DomainEvent {
  id: string;
  type: string;
  payload: Record<string, unknown>;
  at: number; // epoch ms, client receive time
}

export interface DecisionProposal {
  id: string;
  domain: string;
  at: number;
}

export interface PhaseGoal {
  name: string;
  description: string;
}

export interface PhaseProposal {
  name: string;
  goal_names: string[];
  goals: PhaseGoal[];
  at: number;
}

export type PlannerRunKind = 'architecture' | 'phase_review';

const EVENT_BUFFER_MAX = 500;

interface PlannerState {
  // Chat transcript (operator ↔ planner — system noise lives in `events`)
  messages: ChatMessage[];

  // Rolling domain-event buffer (Activity view)
  events: DomainEvent[];

  // Architecture decision proposals captured from SSE
  decisions: DecisionProposal[];

  // Architecture phase proposals (with per-goal descriptions) captured from SSE
  phases: PhaseProposal[];

  // Which autonomous planner run (architecture / phase_review) is in flight,
  // and which kinds have completed this session. Drives the rail's
  // "Draft architecture" / "Run phase review" affordances and gate gating,
  // since these runs have no REST read-model — only SSE start/finish events.
  activeRun: PlannerRunKind | null;
  completedRuns: PlannerRunKind[];

  // SSE connection
  connection: { state: ConnectionState; lastEventAt: number | null };

  // UI
  ui: PlannerUIState;

  // ── Chat ──────────────────────────────────────────────────────────────────
  addMessage: (msg: Omit<ChatMessage, 'id'>) => void;
  setThinking: (v: boolean) => void;

  // ── Stream ────────────────────────────────────────────────────────────────
  pushEvent: (type: string, payload: Record<string, unknown>) => void;
  setConnectionState: (state: ConnectionState) => void;
  addDecision: (d: Omit<DecisionProposal, 'at'>) => void;
  clearDecisions: () => void;
  addPhase: (p: Omit<PhaseProposal, 'at'>) => void;
  clearPhases: () => void;

  // ── Autonomous planner runs ─────────────────────────────────────────────────
  setActiveRun: (kind: PlannerRunKind | null) => void;
  markRunComplete: (kind: PlannerRunKind) => void;
  resetRuns: () => void;

  // ── Selection / panels ────────────────────────────────────────────────────
  selectNode: (id: string | null) => void;
  setLayoutDirection: (dir: 'LR' | 'TB') => void;
  toggleChatPanel: () => void;
  setGateOpen: (open: boolean) => void;
}

export const usePlannerStore = create<PlannerState>()(
  immer((set) => ({
    messages: [],
    events: [],
    decisions: [],
    phases: [],
    activeRun: null,
    completedRuns: [],
    connection: { state: 'connecting', lastEventAt: null },

    ui: {
      selectedNodeId: null,
      selectedGoalId: null,
      detailPanelOpen: false,
      chatPanelCollapsed: false,
      isThinking: false,
      layoutDirection: 'LR',
      gateOpen: false,
    },

    addMessage: (msg) => {
      set((s) => { s.messages.push({ id: nanoid(), ...msg }); });
    },

    setThinking: (v) => {
      set((s) => { s.ui.isThinking = v; });
    },

    pushEvent: (type, payload) => {
      set((s) => {
        s.events.push({ id: nanoid(), type, payload, at: Date.now() });
        if (s.events.length > EVENT_BUFFER_MAX) {
          s.events.splice(0, s.events.length - EVENT_BUFFER_MAX);
        }
        s.connection.lastEventAt = Date.now();
      });
    },

    setConnectionState: (state) => {
      set((s) => { s.connection.state = state; });
    },

    addDecision: (d) => {
      set((s) => {
        if (!s.decisions.some((x) => x.id === d.id)) {
          s.decisions.push({ ...d, at: Date.now() });
        }
      });
    },

    clearDecisions: () => {
      set((s) => { s.decisions = []; });
    },

    addPhase: (p) => {
      set((s) => {
        const existing = s.phases.findIndex((x) => x.name === p.name);
        if (existing >= 0) {
          s.phases[existing] = { ...p, at: Date.now() };
        } else {
          s.phases.push({ ...p, at: Date.now() });
        }
      });
    },

    clearPhases: () => {
      set((s) => { s.phases = []; });
    },

    setActiveRun: (kind) => {
      set((s) => { s.activeRun = kind; });
    },

    markRunComplete: (kind) => {
      set((s) => {
        s.activeRun = null;
        if (!s.completedRuns.includes(kind)) s.completedRuns.push(kind);
      });
    },

    resetRuns: () => {
      set((s) => { s.activeRun = null; s.completedRuns = []; });
    },

    selectNode: (id) => {
      set((s) => {
        s.ui.selectedNodeId = id;
        s.ui.detailPanelOpen = id !== null;
      });
    },

    setLayoutDirection: (dir) => {
      set((s) => { s.ui.layoutDirection = dir; });
    },

    toggleChatPanel: () => {
      set((s) => { s.ui.chatPanelCollapsed = !s.ui.chatPanelCollapsed; });
    },

    setGateOpen: (open) => {
      set((s) => { s.ui.gateOpen = open; });
    },
  })),
);
