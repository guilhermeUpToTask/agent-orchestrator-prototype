"""The plan->markdown context renderer: live vs terminal goals, result
truncation, the capability catalog rendering."""
from __future__ import annotations

from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.capability import Capability
from src.domain.entities.goal import Goal
from src.domain.entities.task import Task
from src.domain.value_objects.tasks_vos import TaskResult
from src.infra.reasoner.runtime.context import (
    render_capabilities,
    render_plan_context,
)


def make_plan():
    done_task = Task(
        id="t1", name="build api", position=0, description="", agent_id="a1"
    )
    done_task.start()
    done_task.complete(TaskResult(status="success", output="built the API " + "x" * 900))
    done_goal = Goal(
        id="g1", name="API", position=0, description="", tasks=[done_task]
    )
    done_goal.start()
    done_goal.complete()

    live_goal = Goal(
        id="g2",
        name="Persistence",
        position=1,
        description="wire storage",
        tasks=[
            Task(
                id="t2",
                name="wire sqlite",
                position=0,
                description="",
                required_capabilities=["backend"],
            )
        ],
    )
    empty_goal = Goal(id="g3", name="Docs", position=2, description="", tasks=[])
    return Plan(
        id="p1",
        brief="tiny service",
        phase=PlanPhase.REPLANNING,
        iteration=1,
        goals=[done_goal, live_goal, empty_goal],
    )


def test_terminal_goals_are_one_liners_and_live_goals_full():
    ctx = render_plan_context(make_plan())

    assert "**Brief**: tiny service" in ctx
    assert "**Phase**: replanning — **Iteration**: 1" in ctx
    # terminal: one-liner with progress, no task detail
    assert "- [done] **API** — 1/1 tasks done" in ctx
    assert "built the API" not in ctx  # results excluded by default
    # live: full rendering with caps + the explicit no-tasks marker
    assert "### Persistence [pending]" in ctx
    assert "- [pending] wire sqlite (caps: backend)" in ctx
    assert "### Docs [pending]" in ctx
    assert "(no tasks yet)" in ctx


def test_results_included_and_truncated_for_replanning():
    ctx = render_plan_context(make_plan(), include_results=True, max_result_chars=50)

    assert "`build api` result: built the API" in ctx
    assert "…[truncated]" in ctx
    # the raw 900-char tail never leaks
    assert "x" * 60 not in ctx


def test_capability_catalog_rendering():
    caps = [
        Capability(id="backend", name="Backend", description="server code"),
        Capability(id="qa", name="QA", description=""),
    ]
    md = render_capabilities(caps)
    assert "`backend`: Backend — server code" in md
    assert "`qa`: QA" in md

    assert "(empty" in render_capabilities([])
