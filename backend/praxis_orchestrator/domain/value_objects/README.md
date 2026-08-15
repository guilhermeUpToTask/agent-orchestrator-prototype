# Value Objects

Immutable/typed building blocks with no identity of their own.

## `Status` + `TERMINAL` (`tasks_vos.py`)
The lifecycle enum shared by **both** `Goal` and `Task` (`PENDING → RUNNING → DONE/…`).
`str`-based so comparisons and JSON are natural. `TERMINAL = {DONE, SKIPPED, FAILED}` — a
`FAILED` node being terminal is what stops the old infinite-loop: it is skipped, never
re-selected forever.

Because it's shared, `tasks_vos` is a slightly misleading home — a neutral
`value_objects/lifecycle.py` would be clearer. It's a rename-only cleanup; see
[`domain-design-decisions.md`](../../../../docs/decisions/domain-design-decisions.md).

## `TaskResult` (`tasks_vos.py`)
The typed output of a task run **and** the idempotency record: if `task.result` is set, the
work already happened and must not re-execute. `status` and `output` are always present
(always assertable in tests); `artifacts` is the flexible per-task-type payload (code task →
`files_changed`, research task → `sources`) so one rigid schema isn't forced on every task
type. This is the seam that makes orchestration deterministically testable — tests fabricate
`TaskResult`s by hand, production builds them from the agent, and the orchestration treats
both identically.

`success()` / `failure()` are convenience constructors. `success(**kw)` is loosely typed
(accepts arbitrary kwargs) — a candidate to tighten to explicit params; see DESIGN_NOTES.
