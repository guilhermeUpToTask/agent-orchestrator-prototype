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
  AgentSpec,
  Capability,
  ChatMessageResponse,
  IaModel,
  MessageResponse,
  ModelProvider,
  Plan,
  PlanSummary,
  SSEPayload,
} from '../types/ui';

const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

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
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
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

const get = <T>(path: string) => request<T>('GET', path);
const post = <T>(path: string, body?: unknown, headers?: Record<string, string>) =>
  request<T>('POST', path, body, headers);

// ─── Plans: lifecycle ─────────────────────────────────────────────────────────

export const listPlans = (): Promise<PlanSummary[]> => get('/api/plans');

export const fetchPlan = (planId: string): Promise<Plan> =>
  get(`/api/plans/${encodeURIComponent(planId)}`);

export const createPlan = (
  brief: string,
  idempotencyKey: string,
): Promise<{ plan_id: string }> =>
  post('/api/plans', { brief }, { 'Idempotency-Key': idempotencyKey });

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

// ─── Plans: conversation (multi-turn with commit) ─────────────────────────────

export const sendDiscoveryMessage = (
  planId: string,
  message: string,
): Promise<MessageResponse> =>
  post(`/api/plans/${encodeURIComponent(planId)}/discovery/message`, { message });

export const sendReplanningMessage = (
  planId: string,
  message: string,
): Promise<MessageResponse> =>
  post(`/api/plans/${encodeURIComponent(planId)}/replanning/message`, { message });

export const fetchChat = (planId: string): Promise<ChatMessageResponse[]> =>
  get(`/api/plans/${encodeURIComponent(planId)}/chat`);

// ─── Plans: structural edits ──────────────────────────────────────────────────

export interface EditBody {
  type:
    | 'add_task'
    | 'remove_task'
    | 'reorder_tasks'
    | 'edit_task_requirements'
    | 'rebind_task_agent';
  goal_id: string;
  task_id?: string;
  task?: { name: string; description?: string; required_capabilities?: string[] };
  ordered_task_ids?: string[];
  required_capabilities?: string[];
  agent_id?: string;
}

export const applyEdit = (planId: string, edit: EditBody): Promise<void> =>
  post(`/api/plans/${encodeURIComponent(planId)}/edits`, edit);

// ─── Reference data ───────────────────────────────────────────────────────────

export const listAgents = (): Promise<AgentSpec[]> => get('/api/agents');
export const listCapabilities = (): Promise<Capability[]> => get('/api/capabilities');
export const listProviders = (): Promise<ModelProvider[]> => get('/api/providers');
export const listModels = (): Promise<IaModel[]> => get('/api/models');

// ─── Two-tier config ──────────────────────────────────────────────────────────

export const getConfigScope = (scope: string): Promise<Record<string, string>> =>
  get(`/api/config/${encodeURIComponent(scope)}`);

export const setConfigKey = (
  scope: string,
  key: string,
  value: string,
): Promise<void> =>
  request('PUT', `/api/config/${encodeURIComponent(scope)}/${encodeURIComponent(key)}`, {
    value,
  });

// ─── SSE subscription ─────────────────────────────────────────────────────────

/** The outbox event vocabulary + the agent telemetry feed. */
export const SSE_EVENT_TYPES = [
  'PhaseAdvanced',
  'TaskStarted',
  'TaskCompleted',
  'TaskRequeued',
  'TaskFailedEvent',
  'TaskAbandoned',
  'ReplanRequested',
  'GoalCompleted',
  'GoalFailedEvent',
  'PlanCompleted',
  'PlanFailed',
  'AgentFellBackToDefault',
  'agent.event',
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
