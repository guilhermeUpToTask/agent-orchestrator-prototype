// src/types/ui.ts
// UI-side composition types for the cyclic ProjectPlan console.
//
// DTO shapes with OpenAPI schemas come from src/types/generated (npm run
// generate:api). The detail view adds narrow status literals and UI conveniences
// over that generated transport contract; keep these aligned with PlanDetailResponse.

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

// ─── Legacy phase compatibility + canonical root status ─────────────────────

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
  retry_cycle?: number;
  cycle_attempt?: number;
  revision?: number;
  role_agent_ids?: Record<string, string>;
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

export interface PlanBlock {
  id: string;
  kind: string;
  explanation: string;
  stage: string;
  goal_id: string | null;
  task_id: string | null;
  task_revision: number | null;
  run_id: string | null;
  evidence_refs: string[];
  legal_resolutions: string[];
  created_at: string;
  resolved_at: string | null;
  resolution: string | null;
}

export interface IntentProposal {
  id: string;
  kind: "initial" | "replan";
  base_plan_version: number;
  source_cycle_id: string | null;
  objective: string;
  scope: string[];
  constraints: string[];
  exclusions: string[];
  revision: number;
  planner_session_ref: string | null;
  approved_at: string | null;
  cancelled_at: string | null;
}

export interface GoalOutline {
  key: string;
  name: string;
  objective: string;
  position: number;
  depends_on: string[];
}

export interface CycleDraft {
  id: string;
  intent_proposal_id: string;
  base_plan_version: number;
  source_cycle_id: string | null;
  goals: GoalOutline[];
  revision: number;
  unfinished_source_treatment: string | null;
  approved_at: string | null;
  cancelled_at: string | null;
}

export interface Cycle {
  id: string;
  intent_proposal_id: string;
  draft_id: string;
  status: "active" | "completed" | "superseded" | "cancelled";
  goals: Goal[];
  started_at: string;
  completed_at: string | null;
  superseded_at: string | null;
  cancelled_at: string | null;
  evidence_refs: string[];
  output_disposition: "open_pr" | "merge" | "retain_branch" | "discard" | null;
  output_reference: string | null;
}

export type ActiveCycle = Cycle;

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
  planning_operation: {
    id: string;
    purpose: string;
    target_goal_id: string | null;
    status: string;
    updated_at: string;
    retry_at: string | null;
    safe_message: string | null;
  } | null;
  planning_progress: string | null;
  active_cycle: ActiveCycle | null;
  pending_gate: PendingGate | null;
  block: PlanBlock | null;
  cycles: Cycle[];
  intent_proposal: IntentProposal | null;
  cycle_draft: CycleDraft | null;
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
