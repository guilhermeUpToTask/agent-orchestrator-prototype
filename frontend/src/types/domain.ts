// src/types/domain.ts
// Mirrors src/domain/ Python types 1-to-1

export type TaskStatus =
  | 'created' | 'assigned' | 'in_progress'
  | 'succeeded' | 'failed' | 'canceled'
  | 'requeued' | 'merged';

export type GoalStatus =
  | 'pending' | 'running' | 'ready_for_review'
  | 'awaiting_pr_approval' | 'approved' | 'merged'
  | 'failed' | 'completed';

export type ProjectPlanStatus =
  | 'discovery' | 'architecture'
  | 'phase_active' | 'phase_review' | 'done';

export type PhaseStatus = 'planned' | 'active' | 'completed';

// ─── Backend API shapes ────────────────────────────────────────────────────────
// These match what _goal_to_dict / _plan_to_dict return from the API

export interface TaskSummary {
  task_id: string;
  title: string;
  status: TaskStatus;
  assigned_agent_id: string | null;
  retry_count: number;
}

export interface GoalAggregate {
  goal_id: string;
  name: string;
  description: string;
  status: GoalStatus;
  feature_tag: string | null;
  depends_on: string[];
  tasks: TaskSummary[];
  history: HistoryEntry[];
}

export interface Phase {
  index: number;
  name: string;
  goal: string;
  goal_names: string[];
  status: PhaseStatus;
  exit_criteria: string;
  lessons: string;
}

export interface ProjectBrief {
  vision: string;
  constraints: string[];
  phase_1_exit_criteria: string;
  open_questions: string[];
}

export interface ProjectPlan {
  plan_id: string | null;
  status: ProjectPlanStatus;
  vision: string;
  architecture_summary: string;
  current_phase_index: number;
  state_version: number;
  phases: Phase[];
  brief: ProjectBrief | null;
  history: HistoryEntry[];
}

export interface AgentProps {
  agent_id: string;
  name: string;
  capabilities: string[];
  version: string;
  trust_level: string;
  active: boolean;
  max_concurrent_tasks: number;
  tools: string[];
  // UI helper (derived)
  color?: string;
}

export interface HistoryEntry {
  event: string;
  actor: string;
  detail: Record<string, unknown> | null;
  ts: string;
}

// ─── React Flow node payload ───────────────────────────────────────────────────

export interface TaskNodeData {
  task: TaskSummary;
  goalId: string;
  goalName: string;
  agent: AgentProps | null;
  selected?: boolean;
}

// ─── Chat ──────────────────────────────────────────────────────────────────────

export type ChatRole = 'user' | 'assistant' | 'system';

export interface ChatMessage {
  id: string;
  role: ChatRole;
  text: string;
  ts: string;
  nodeCtx?: string;
}

// ─── UI state ──────────────────────────────────────────────────────────────────

export interface PlannerUIState {
  selectedNodeId: string | null;
  selectedGoalId: string | null;
  detailPanelOpen: boolean;
  chatPanelCollapsed: boolean;
  isThinking: boolean;
  layoutDirection: 'LR' | 'TB';
}
