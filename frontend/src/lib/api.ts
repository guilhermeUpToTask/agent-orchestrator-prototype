/**
 * src/lib/api.ts
 *
 * All HTTP calls to the AIPOM backend.
 * Routes chat messages by plan status — no direct Anthropic calls from the browser.
 */

import type {
  ProjectPlan,
  GoalAggregate,
  AgentProps,
  HistoryEntry,
  ProjectPlanStatus,
} from '../types/ui';
import type {
  ApproveArchitectureResponse,
  ApproveBriefResponse,
  ApprovePhaseResponse,
  SessionAccepted,
  SessionStatusResponse,
} from '../types/generated';

export type {
  ApproveArchitectureResponse,
  ApproveBriefResponse,
  ApprovePhaseResponse,
  SessionAccepted,
  SessionStatusResponse,
};

/** Discovery turn as the chat layer consumes it (question or completion). */
export interface DiscoveryTurn {
  question: string | null;
  done: boolean;
  brief: Record<string, unknown> | null;
}

/** Refinement outcome as the chat layer consumes it. */
export interface RefineResponse {
  session_id: string;
  actions_taken: string[];
  succeeded: boolean;
  error: string | null;
}

const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

// ─── Helpers ──────────────────────────────────────────────────────────────────

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`POST ${path} → ${res.status}: ${text}`);
  }
  return res.json();
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`GET ${path} → ${res.status}: ${text}`);
  }
  return res.json();
}

// ─── M1: Read ─────────────────────────────────────────────────────────────────

export const fetchPlan = (): Promise<ProjectPlan> =>
  get('/api/plan');

export const fetchGoals = (): Promise<GoalAggregate[]> =>
  get('/api/goals');

export const fetchAgents = (): Promise<AgentProps[]> =>
  get('/api/agents');

export const fetchPlanHistory = (): Promise<HistoryEntry[]> =>
  get('/api/plan/history');

export const fetchGoalHistory = (goalId: string): Promise<HistoryEntry[]> =>
  get(`/api/goals/${goalId}/history`);

// ─── M2: Lifecycle approvals ──────────────────────────────────────────────────

export const approveBrief = (): Promise<ApproveBriefResponse> =>
  post('/api/plan/approve-brief');

export const approveArchitecture = (
  decisionIds: string[],
): Promise<ApproveArchitectureResponse> =>
  post('/api/plan/approve-architecture', { decision_ids: decisionIds });

export const approvePhase = (
  approveNext = true,
): Promise<ApprovePhaseResponse> =>
  post('/api/plan/approve-phase', { approve_next: approveNext });

// ─── Autonomous planner runs (202; progress + completion stream over SSE) ─────

/**
 * Kick off the autonomous architecture planner. Returns immediately (202);
 * proposed decisions arrive as `plan.decision_proposed` SSE events and the
 * run ends with `plan.architecture_completed` / `plan.architecture_failed`.
 * This is the step that was missing over HTTP — without it the plan sat in
 * `architecture` forever and approve-architecture 409'd.
 */
export const runArchitecture = (): Promise<SessionAccepted> =>
  post('/api/plan/architecture/run');

/** Kick off the autonomous phase-review planner (same 202 + SSE shape). */
export const runPhaseReview = (): Promise<SessionAccepted> =>
  post('/api/plan/phase-review/run');

// ─── Long-running sessions (202 + poll; progress also streams over SSE) ──────

const SESSION_POLL_INTERVAL_MS = 750;
const SESSION_POLL_TIMEOUT_MS = 10 * 60 * 1000;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function pollSession(
  url: string,
  until: (s: SessionStatusResponse) => boolean,
): Promise<SessionStatusResponse> {
  const deadline = Date.now() + SESSION_POLL_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const session = await get<SessionStatusResponse>(url);
    if (until(session)) return session;
    await sleep(SESSION_POLL_INTERVAL_MS);
  }
  throw new Error(`Session at ${url} did not settle within the poll timeout`);
}

const isSettled = (s: SessionStatusResponse) =>
  s.status === 'waiting_input' || s.status === 'done' || s.status === 'failed';

function toDiscoveryTurn(session: SessionStatusResponse): DiscoveryTurn {
  if (session.status === 'failed') {
    throw new Error(session.error ?? 'Discovery session failed');
  }
  return {
    question: session.question ?? null,
    done: session.status === 'done',
    brief: (session.result?.brief as Record<string, unknown> | undefined) ?? null,
  };
}

// The active discovery session, established by startDiscovery().
let activeDiscoverySessionId: string | null = null;

/** True while an interactive discovery session is running/awaiting answers. */
export const hasActiveDiscoverySession = (): boolean =>
  activeDiscoverySessionId !== null;

// ─── M3: Chat routing by plan status ─────────────────────────────────────────

/**
 * Route a chat message to the correct backend endpoint based on current plan status.
 *
 * PHASE_ACTIVE  → POST /api/plan/refine (202), result via GET /plan/sessions/{id}
 * DISCOVERY     → POST /api/plan/discovery/{id}/message (202), next turn via GET
 * PHASE_REVIEW  → returns advisory text — operator should use approval buttons
 * Others        → returns advisory text
 */
export async function sendChatMessage(
  message: string,
  planStatus: ProjectPlanStatus,
  focusedNodeId: string | null,
  focusedGoalId: string | null,
): Promise<{ type: 'refinement'; data: RefineResponse }
         | { type: 'discovery'; data: DiscoveryTurn }
         | { type: 'advisory'; text: string }> {

  switch (planStatus) {
    case 'phase_active': {
      const accepted = await post<SessionAccepted>('/api/plan/refine', {
        message,
        focused_node_id: focusedNodeId,
        focused_goal_id: focusedGoalId,
      });
      const session = await pollSession(
        `/api/plan/sessions/${accepted.session_id}`,
        (s) => s.status === 'done' || s.status === 'failed',
      );
      const outcome = session.result ?? {};
      const data: RefineResponse = {
        session_id: session.session_id,
        actions_taken: (outcome.actions_taken as string[] | undefined) ?? [],
        succeeded: session.status === 'done',
        error: session.error ?? null,
      };
      return { type: 'refinement', data };
    }

    case 'discovery': {
      if (!activeDiscoverySessionId) {
        return {
          type: 'advisory',
          text: 'No discovery session is running. Use "Start Discovery" first.',
        };
      }
      const base = `/api/plan/discovery/${activeDiscoverySessionId}`;
      await post<SessionAccepted>(`${base}/message`, { message });
      const session = await pollSession(base, isSettled);
      if (session.status === 'done' || session.status === 'failed') {
        activeDiscoverySessionId = null;
      }
      return { type: 'discovery', data: toDiscoveryTurn(session) };
    }

    case 'architecture':
      return {
        type: 'advisory',
        text: 'Architecture is being drafted by the planner. Once it completes, use "Approve Architecture" to select decisions and activate the phase.',
      };

    case 'phase_review':
      return {
        type: 'advisory',
        text: 'The phase review is running. Once it completes, use "Approve Phase" to move to the next phase or mark the project done.',
      };

    case 'done':
      return {
        type: 'advisory',
        text: 'The project is complete. All phases have been executed and merged.',
      };

    default:
      return {
        type: 'advisory',
        text: `Plan is in "${planStatus}" state. No chat actions are available right now.`,
      };
  }
}

// ─── M4: Discovery session start ──────────────────────────────────────────────

/**
 * Start the discovery session (202 + session id) and wait for the first
 * turn: either the first question or immediate completion with the brief.
 */
export async function startDiscovery(): Promise<DiscoveryTurn> {
  const accepted = await post<SessionAccepted>('/api/plan/discovery/start');
  activeDiscoverySessionId = accepted.session_id;
  try {
    const session = await pollSession(
      `/api/plan/discovery/${accepted.session_id}`,
      isSettled,
    );
    if (session.status === 'done' || session.status === 'failed') {
      activeDiscoverySessionId = null;
    }
    return toDiscoveryTurn(session);
  } catch (err) {
    activeDiscoverySessionId = null;
    throw err;
  }
}

// ─── M5: SSE subscription ─────────────────────────────────────────────────────

export type SSEEvent =
  | { type: 'plan.status_changed'; payload: { status: string } }
  | { type: 'goal.dispatched'; payload: { goal_id: string } }
  | { type: 'task.status_changed'; payload: { task_id: string; status: string } }
  | { type: 'plan.jit_progress'; payload: Record<string, unknown> }
  | { type: 'plan.refinement_action'; payload: { action: string } }
  // Planner tool calls forwarded through the planner event hook
  | { type: 'plan.decision_proposed'; payload: { id: string; domain: string } }
  | { type: 'plan.phase_proposed'; payload: { name: string; goal_names: string[] } }
  // Autonomous architecture / phase-review run lifecycle (202 + SSE)
  | { type: 'plan.architecture_completed'; payload: { session_id: string } }
  | { type: 'plan.architecture_failed'; payload: { session_id: string; error: string | null } }
  | { type: 'plan.phase_review_completed'; payload: { session_id: string } }
  | { type: 'plan.phase_review_failed'; payload: { session_id: string; error: string | null } }
  // GitHub PR gate lifecycle
  | { type: 'goal.pr_opened'; payload: { goal_id: string; pr_number: number | null } }
  | { type: 'goal.pr_state_synced'; payload: { goal_id: string } }
  | { type: 'goal.finalized'; payload: { goal_id: string } };
// Note: the backend bridge may forward domain events outside this union
// verbatim; consumers handle those in a default branch at runtime.

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

export function subscribeToEvents(cb: SubscribeCallbacks): () => void {
  let es: EventSource | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let hadError = false;
  let closed = false;

  function connect() {
    es = new EventSource(`${BASE}/api/events`);

    es.onopen = () => {
      if (hadError) {
        // Events emitted while disconnected are gone — resync via refetch.
        hadError = false;
        cb.onReconnect?.();
      } else {
        cb.onOpen?.();
      }
    };

    es.onmessage = (e) => {
      try {
        const parsed = JSON.parse(e.data) as SSEEvent;
        cb.onEvent(parsed);
      } catch {
        // ignore malformed events
      }
    };

    es.onerror = () => {
      hadError = true;
      if (es?.readyState === EventSource.CLOSED) {
        // Hard close — EventSource won't retry on its own. Recreate.
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
