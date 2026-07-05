// src/types/ui.ts
// UI-side types for the 9-phase orchestrator.
//
// DTO shapes with OpenAPI schemas come from src/types/generated (npm run
// generate:api). The plan DETAIL endpoint returns the aggregate document
// (untyped `object` in the schema), so its read model is declared here by
// hand — keep it in sync with backend/src/domain (Plan/Goal/Task).

import type {
  AgentBody,
  AgentSpec,
  Capability,
  ChatMessageResponse,
  DefaultAgentResponse,
  FailureKind,
  IaModel,
  MessageResponse,
  ModelProvider,
  ProjectDefinition,
  ProviderCreateBody,
  ProviderUpdateBody,
  ReasonerStatusResponse,
  RetryPolicy,
  RunnerAgentStatus,
  RunnerBinaryStatus,
  RunnerStatusResponse,
} from './generated';

export type {
  AgentBody,
  AgentSpec,
  Capability,
  ChatMessageResponse,
  DefaultAgentResponse,
  FailureKind,
  IaModel,
  MessageResponse,
  ModelProvider,
  ProjectDefinition,
  ProviderCreateBody,
  ProviderUpdateBody,
  ReasonerStatusResponse,
  RetryPolicy,
  RunnerAgentStatus,
  RunnerBinaryStatus,
  RunnerStatusResponse,
};

// ─── The 9-phase machine ────────────────────────────────────────────────────

export type PlanPhase =
  | 'discovery'
  | 'replanning'
  | 'architecture'
  | 'enriching'
  | 'awaiting_review'
  | 'running'
  | 'review'
  | 'done'
  | 'failed';

export type Status = 'pending' | 'running' | 'done' | 'failed' | 'skipped';

// ─── Plan aggregate read model (GET /api/plans/{id}) ────────────────────────

export interface TaskResult {
  status: 'success' | 'failure';
  output: string;
  artifacts: Record<string, string>;
  failure_reason: string | null;
  failure_kind: string | null;
  metadata: Record<string, string>;
}

export interface Task {
  id: string;
  name: string;
  position: number;
  description: string;
  required_capabilities: string[];
  agent_id: string | null;
  status: Status;
  result: TaskResult | null;
  attempt: number;
  reopen_count: number;
  retry_not_before: string | null;
}

export interface Goal {
  id: string;
  name: string;
  position: number;
  description: string;
  status: Status;
  tasks: Task[];
  depends_on: string[];
}

export interface Plan {
  id: string;
  brief: string;
  phase: PlanPhase;
  iteration: number;
  version: number;
  goals: Goal[];
}

/** GET /api/plans — cheap listing off the promoted columns. */
export interface PlanSummary {
  id: string;
  phase: PlanPhase;
  iteration: number;
  version: number;
  claimed_by: string | null;
  updated_at: string;
}

// ─── SSE event payloads (relay-fed; every payload carries event_id) ─────────

export interface SSEPayload {
  event_id: string;
  plan_id: string;
  [key: string]: unknown;
}

// ─── Chat (server history + UI decoration) ─────────────────────────────────

export type ChatRole = 'user' | 'assistant' | 'system';

export interface UIChatMessage {
  id: string;
  role: ChatRole;
  text: string;
  ts: string;
  committed?: boolean;
}

// ─── React Flow node payloads ───────────────────────────────────────────────

export interface TaskNodeData {
  task: Task;
  goalId: string;
  goalName: string;
  agent: AgentSpec | null;
  selected?: boolean;
  [key: string]: unknown;
}

// ─── UI state ──────────────────────────────────────────────────────────────

export interface PlannerUIState {
  selectedTaskId: string | null;
  detailPanelOpen: boolean;
  chatPanelCollapsed: boolean;
  layoutDirection: 'LR' | 'TB';
  gateOpen: boolean;
  consoleOpen: boolean;
}
