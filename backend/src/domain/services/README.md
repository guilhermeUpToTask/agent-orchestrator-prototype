# Services

Pure domain logic that spans more than one entity, or that is shared by the aggregate and
the edit rules. No I/O, no state.

## `navigation.py` — `next_action(goals, now)`
The derive-don't-store scan (full contract in the domain [`README`](../README.md)). One
subtlety worth stating: **a failed task does not immediately fail its goal.** The scan
first drains every *actionable* task (non-terminal, past its backoff gate). Only when a
goal has no actionable tasks left **and** at least one `FAILED` task does it return
`(goal, "GOAL_FAILED")` — a *signal* the worker turns into `Plan.fail_goal()`. A task that
failed but still has retries left is still actionable, so a transient failure never reaches
that branch; only exhausted retries fail the goal.

## `edit_service.py` — structural edits
Edit *validation rules* are domain logic (they span the plan structure and the request), so
they live here, not on the aggregate. A goal that is `RUNNING` or terminal is not editable —
which is also why the scan can never desync: you can only edit work not yet started.

- **`_renumber`** reassigns contiguous `0..n-1` positions after an add/remove (which can
  leave gaps like `[0,2]`), preserving order — so the scan/UI can rely on dense positions.
- **`remove_task`** filters by id rather than `list.remove/pop` (which need the object or an
  index) so a miss raises a typed `InvalidEditError`, not a bare `ValueError`.
- **`reorder_tasks`** takes the goal's task ids in the desired order and writes each task's
  `position` to its index there. The id *set* is only validated (must equal the goal's task
  ids) — position stays the sort key, not id. It operates within a single goal.
- **`edit_task_requirements`** changes a task's capability ids without re-matching the agent
  (snapshot binding stays; execution re-validates).

## `lookups.py` — `find_goal` / `find_task`
Pure in-memory traversals of an **already-loaded** aggregate, not database queries. There
is no goal/task repo: goals and tasks aren't independently persisted — they're owned by the
`Plan` aggregate root and loaded/saved whole by `PlanRepository`. Extracted into a shared
service so the aggregate and `edit_service` use one find-or-raise implementation with
consistent domain errors (DRY).

## `capability_matching.py` — `match_agent`
First agent whose capability ids cover the task's required ids; returns
`(agent_id, used_default)`. A free function, not a `Task` method, so it's trivially testable.
