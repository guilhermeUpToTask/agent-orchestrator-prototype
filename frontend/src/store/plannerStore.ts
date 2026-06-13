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

const EVENT_BUFFER_MAX = 500;

interface PlannerState {
  // Chat transcript (operator ↔ planner — system noise lives in `events`)
  messages: ChatMessage[];

  // Rolling domain-event buffer (Activity view)
  events: DomainEvent[];

  // Architecture decision proposals captured from SSE
  decisions: DecisionProposal[];

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
