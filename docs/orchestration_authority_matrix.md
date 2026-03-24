# Orchestration Authority Matrix

Defines which component is authoritative for each state transition.

| Transition / Action | Authoritative component | Notes |
|---|---|---|
| `task.created/requeued -> task.assigned` | `TaskAssignUseCase` via task-manager | Scheduler + lease creation path |
| `task.assigned -> task.in_progress` | `TaskExecuteUseCase` via worker | CAS retry in worker |
| `task.in_progress -> task.succeeded/failed` | `TaskExecuteUseCase` | Emits `task.completed` / `task.failed` |
| `task.failed -> task.requeued/canceled` | `TaskFailHandlingUseCase` | Retry policy gate |
| `task.succeeded -> task.merged` | `GoalMergeTaskUseCase` | Merge task branch into goal branch |
| `goal.pending -> goal.running` | `TaskGraphOrchestrator` / `UnblockGoalsUseCase` | Start on assignment or dependency release |
| `goal.running -> goal.ready_for_review` | `GoalMergeTaskUseCase` | After all goal tasks merged |
| `goal.ready_for_review -> awaiting_pr_approval` | `CreateGoalPRUseCase` | Idempotent open/adopt PR |
| PR-state sync into goal fields | `SyncGoalPRStatusUseCase` | Poll-only, no progression decisions |
| PR-driven goal status transition | `AdvanceGoalFromPRUseCase` | Emits `goal.approved` / `goal.merged` |
| `project_plan.phase_active -> phase_review` | `AdvanceGoalFromPRUseCase._check_phase_completion` | CAS update_if_version on plan repository |

## Guardrails

1. Domain aggregates are the only place where status mutation rules live.
2. Adapters MUST NOT mutate aggregate fields directly.
3. Event consumers acknowledge (`ack`) only after successful handling.
4. CI should keep unit tests for each authoritative transition path.
