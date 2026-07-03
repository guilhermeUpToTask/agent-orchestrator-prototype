/**
 * src/store/plannerStore.ts
 *
 * Local UI state ONLY. All server state (plans, plan aggregate, chat,
 * reference data) lives in React Query (src/lib/queries.ts) and is kept
 * fresh by SSE cache invalidation. This store holds what the server
 * doesn't: selection, panel visibility, the SSE connection state, the
 * rolling event buffer (Activity) and the live agent log (ConsoleDock).
 */

import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import { nanoid } from 'nanoid';

import type { PlannerUIState, SSEPayload } from '../types/ui';

export type ConnectionState = 'connecting' | 'live' | 'reconnecting' | 'down';

export interface BufferedEvent {
  id: string;
  type: string;
  payload: Record<string, unknown>;
  at: number; // epoch ms, client receive time
}

export interface AgentLogLine {
  id: string;
  plan_id: string;
  task_id: string;
  attempt: number;
  seq: number;
  type: string;
  text: string;
  at: number;
}

const EVENT_BUFFER_MAX = 500;
const AGENT_LOG_MAX = 400;
const SEEN_IDS_MAX = 2000;

interface PlannerState {
  // Rolling domain-event buffer (Activity view)
  events: BufferedEvent[];
  // Live agent telemetry (ConsoleDock), from "agent.event"
  agentLog: AgentLogLine[];
  // event_id dedup ring (delivery is at-least-once)
  seenEventIds: string[];

  connection: { state: ConnectionState; lastEventAt: number | null };
  ui: PlannerUIState;

  /** Buffer an event; returns false when this event_id was already seen. */
  pushEvent: (type: string, payload: SSEPayload) => boolean;
  appendAgentLog: (payload: SSEPayload) => void;
  setConnectionState: (state: ConnectionState) => void;

  selectTask: (id: string | null) => void;
  setLayoutDirection: (dir: 'LR' | 'TB') => void;
  toggleChatPanel: () => void;
  setGateOpen: (open: boolean) => void;
  toggleConsole: () => void;
}

export const usePlannerStore = create<PlannerState>()(
  immer((set, get) => ({
    events: [],
    agentLog: [],
    seenEventIds: [],
    connection: { state: 'connecting', lastEventAt: null },

    ui: {
      selectedTaskId: null,
      detailPanelOpen: false,
      chatPanelCollapsed: false,
      layoutDirection: 'LR',
      gateOpen: false,
      consoleOpen: false,
    },

    pushEvent: (type, payload) => {
      const eventId = payload.event_id;
      if (eventId && get().seenEventIds.includes(eventId)) {
        set((s) => {
          s.connection.lastEventAt = Date.now();
        });
        return false;
      }
      set((s) => {
        if (eventId) {
          s.seenEventIds.push(eventId);
          if (s.seenEventIds.length > SEEN_IDS_MAX) {
            s.seenEventIds.splice(0, s.seenEventIds.length - SEEN_IDS_MAX);
          }
        }
        s.events.push({ id: nanoid(), type, payload, at: Date.now() });
        if (s.events.length > EVENT_BUFFER_MAX) {
          s.events.splice(0, s.events.length - EVENT_BUFFER_MAX);
        }
        s.connection.lastEventAt = Date.now();
      });
      return true;
    },

    appendAgentLog: (payload) => {
      set((s) => {
        s.agentLog.push({
          id: (payload.event_id as string) ?? nanoid(),
          plan_id: payload.plan_id,
          task_id: (payload.task_id as string) ?? '',
          attempt: (payload.attempt as number) ?? 0,
          seq: (payload.seq as number) ?? 0,
          type: (payload.type as string) ?? 'step',
          text: JSON.stringify(payload.payload ?? {}),
          at: Date.now(),
        });
        if (s.agentLog.length > AGENT_LOG_MAX) {
          s.agentLog.splice(0, s.agentLog.length - AGENT_LOG_MAX);
        }
      });
    },

    setConnectionState: (state) => {
      set((s) => {
        s.connection.state = state;
      });
    },

    selectTask: (id) => {
      set((s) => {
        s.ui.selectedTaskId = id;
        s.ui.detailPanelOpen = id !== null;
      });
    },

    setLayoutDirection: (dir) => {
      set((s) => {
        s.ui.layoutDirection = dir;
      });
    },

    toggleChatPanel: () => {
      set((s) => {
        s.ui.chatPanelCollapsed = !s.ui.chatPanelCollapsed;
      });
    },

    setGateOpen: (open) => {
      set((s) => {
        s.ui.gateOpen = open;
      });
    },

    toggleConsole: () => {
      set((s) => {
        s.ui.consoleOpen = !s.ui.consoleOpen;
      });
    },
  })),
);
