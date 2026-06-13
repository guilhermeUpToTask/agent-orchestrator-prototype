/**
 * src/store/plannerStore.ts
 *
 * Local UI state ONLY. All server state (plan, goals, agents, history)
 * lives in React Query (src/lib/queries.ts) and is kept fresh by SSE
 * cache invalidation. This store holds what the server doesn't know:
 * selection, layout direction, panel visibility, and the chat transcript.
 */

import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import { nanoid } from 'nanoid';

import type { ChatMessage, PlannerUIState } from '../types/ui';

export function ts() {
  return new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
}

interface PlannerState {
  // Chat transcript (interaction log — not server state)
  messages: ChatMessage[];

  // UI
  ui: PlannerUIState;

  // ── Chat ───────────────────────────────────────────────────────────────────
  addMessage: (msg: Omit<ChatMessage, 'id'>) => void;
  setThinking: (v: boolean) => void;

  // ── Selection ──────────────────────────────────────────────────────────────
  selectNode: (id: string | null) => void;

  // ── Layout / panels ────────────────────────────────────────────────────────
  setLayoutDirection: (dir: 'LR' | 'TB') => void;
  toggleChatPanel: () => void;
}

export const usePlannerStore = create<PlannerState>()(
  immer((set) => ({
    messages: [],

    ui: {
      selectedNodeId: null,
      selectedGoalId: null,
      detailPanelOpen: false,
      chatPanelCollapsed: false,
      isThinking: false,
      layoutDirection: 'LR',
    },

    addMessage: (msg) => {
      set((s) => { s.messages.push({ id: nanoid(), ...msg }); });
    },

    setThinking: (v) => {
      set((s) => { s.ui.isThinking = v; });
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
  })),
);
