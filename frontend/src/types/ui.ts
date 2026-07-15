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
} from "./generated";

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
  | "discovery"
  | "replanning"
  | "architecture"
  | "enriching"
  | "awaiting_review"
  | "running"
  | "review"
  | "done"
  | "failed";

export type PlanStatus = "running" | "paused" | "waiting" | "blocked" | "idle";

export type Status = "pending" | "running" | "done" | "failed" | "skipped";

// ─── Plan aggregate read model (GET /api/plans/{id}) ────────────────────────

export interface TaskResult {
  status: "success" | "failure";
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

export interface PendingGate {
  id: string;
  subject_type: "intent" | "cycle_draft" | "cycle_completion" | string;
  subject_id: string;
  subject_revision: number;
  allowed_decisions: string[];
  continuation: string;
}

export interface ActiveCycle {
  id: string;
  [key: string]: unknown;
}

export interface Plan {
  id: string;
  project_id: string | null;
  status: PlanStatus;
  status_reason: { kind: string; code: string | null; message: string | null };
  activity: string;
  current_goal_id: string | null;
  current_task_id: string | null;
  tdd_stage: string | null;
  legal_actions: string[];
  pause_requested: boolean;
  active_run: {
    run_id: string;
    attempt_id: string;
    attempt_number: number;
    goal_id: string;
    task_id: string;
    started_at: string;
  } | null;
  active_cycle: ActiveCycle | null;
  pending_gate: PendingGate | null;
  block: Record<string, unknown> | null;
  brief: string;
  phase: PlanPhase;
  iteration: number;
  version: number;
  goals: Goal[];
  /** Human/auto pause gate (un-freeze #3): unclaimable + editable while true. */
  paused: boolean;
  paused_reason: string | null;
}

/** GET /api/plans — cheap listing off the promoted columns. */
export interface PlanSummary {
  id: string;
  project_id: string | null;
  status: PlanStatus;
  pause_requested: boolean;
  phase: PlanPhase;
  iteration: number;
  version: number;
  claimed_by: string | null;
  updated_at: string;
  paused: boolean;
}

// ─── SSE event payloads (relay-fed; every payload carries event_id) ─────────

export interface SSEPayload {
  event_id: string;
  plan_id: string;
  [key: string]: unknown;
}

// ─── Chat (server history + UI decoration) ─────────────────────────────────

export type ChatRole = "user" | "assistant" | "system";

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
  layoutDirection: "LR" | "TB";
  gateOpen: boolean;
  consoleOpen: boolean;
}
