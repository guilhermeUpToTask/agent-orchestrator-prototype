# Errors

One exception per failure case, all subclassing `DomainError` (← `BaseAppException`).
Each carries a stable `code` (for programmatic handling / API mapping) and a
log-safe `context` dict. Never put secrets in `context`.

| Exception | code | When |
|---|---|---|
| **tasks_errors.py** | | |
| `GoalNotFoundError` | GOAL_NOT_FOUND | goal id not in the plan |
| `TaskNotFoundError` | TASK_NOT_FOUND | task id not in the goal |
| `InvalidTransitionError` | INVALID_TRANSITION | illegal state transition attempted |
| `GoalAlreadyRunningError` | GOAL_ALREADY_RUNNING | edit rejected — goal running/terminal |
| `StaleVersionError` | STALE_VERSION | optimistic-lock conflict (worker-vs-edit race) |
| **agent_errors.py** | | |
| `UnknownCapabilityError` | UNKNOWN_CAPABILITY | capability tag not registered |
| `AgentNotFoundError` | AGENT_NOT_FOUND | task references a deleted agent (reactive net) |
| `CapabilityNoLongerSatisfiedError` | CAPABILITY_NO_LONGER_SATISFIED | bound agent no longer covers the task |
| `NoDefaultAgentError` | NO_DEFAULT_AGENT | fallback needed but no default configured |
| **config_errors.py** | | |
| `ModelNotFoundError` / `ModelProviderNotFoundError` / `CapabilityNotFoundError` | *_NOT_FOUND | reference lookup miss |
| `EntityAlreadyExistsError` | ENTITY_ALREADY_EXISTS | create with a duplicate id |
| `ReferencedEntityInUseError` | ENTITY_IN_USE | delete-guard: still referenced by something active |
| **planning_errors.py** | | |
| `EmptyPlanError` | EMPTY_PLAN | plan created without a brief (birth invariant) |
| `InvalidEditError` | INVALID_EDIT | malformed structural edit |
| `PlanAlreadyTerminalError` | PLAN_ALREADY_TERMINAL | mutation on a DONE/FAILED plan |

## Integrity rules these encode (for the infra adapters)
- **Delete-guard** (`ReferencedEntityInUseError`): refuse to delete an agent/model/
  provider/capability still referenced by a non-terminal task or active plan.
- **Cascade vs guard**: a provider delete CASCADES to its models, but is GUARDED if
  a model is in use by an active agent (cascade down, guard up).
- **Dangling reference net** (`AgentNotFoundError`): even with the guard, execution
  re-checks and fails cleanly if an agent reference is somehow dangling.
