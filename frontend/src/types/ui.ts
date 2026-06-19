// src/types/ui.ts
// UI-side types. All backend DTO shapes come from src/types/generated,
// produced by `npm run generate:api` from the FastAPI OpenAPI schema —
// never hand-write a type that mirrors a backend schema here.

import type {
  AgentResponse,
  GoalResponse,
  GoalTaskResponse,
  PlanBriefResponse,
  PlanHistoryEntryResponse,
  PlanPhaseResponse,
  PlanResponse,
} from './generated';

// Backend enums, re-exported for convenience
export type {
  GoalStatus,
  PhaseStatus,
  ProjectPlanStatus,
  TaskStatus,
} from './generated';

// Legacy component-facing names mapped onto generated DTOs
export type TaskSummary = GoalTaskResponse;
export type GoalAggregate = GoalResponse;
export type Phase = PlanPhaseResponse;
export type ProjectBrief = PlanBriefResponse;
export type ProjectPlan = PlanResponse;
export type HistoryEntry = PlanHistoryEntryResponse;

/** Agent read-model plus the UI-derived display color */
export type AgentProps = AgentResponse & { color?: string };

// ─── React Flow node payload ───────────────────────────────────────────────────

export interface TaskNodeData {
  task: TaskSummary;
  goalId: string;
  goalName: string;
  agent: AgentProps | null;
  // Unmet dependency task_ids — non-empty means this CREATED task is blocked
  // (waiting on siblings), not merely idle in the queue.
  blockedBy: string[];
  selected?: boolean;
  // React Flow v12 node data must satisfy Record<string, unknown>
  [key: string]: unknown;
}

// ─── Chat ──────────────────────────────────────────────────────────────────────

export type ChatRole = 'user' | 'assistant' | 'system' | 'tool';

export interface ChatMessage {
  id: string;
  role: ChatRole;
  text: string;
  ts: string;
  nodeCtx?: string;
  /** Planner tool name when role === 'tool' (e.g. propose_decision) */
  toolName?: string;
}

/**
 * What the chat panel is acting as, derived from ProjectPlanStatus.
 * Mirrors the backend prompt builders (discovery / architecture /
 * phase_review / tactical refinement).
 */
export interface ChatMode {
  key: 'discovery' | 'tactical' | 'awaiting-architecture' | 'awaiting-phase-review' | 'done';
  label: string;
  inputEnabled: boolean;
  hint: string;
}

// ─── UI state ──────────────────────────────────────────────────────────────────

export interface PlannerUIState {
  selectedNodeId: string | null;
  selectedGoalId: string | null;
  detailPanelOpen: boolean;
  chatPanelCollapsed: boolean;
  isThinking: boolean;
  layoutDirection: 'LR' | 'TB';
  /** Approval gate panel (brief / architecture / phase review) */
  gateOpen: boolean;
  /** Live agent-output console dock (bottom of the canvas) is expanded */
  consoleOpen: boolean;
}
