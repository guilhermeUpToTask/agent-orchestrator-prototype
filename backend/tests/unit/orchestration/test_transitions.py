"""Exhaustive state-transition tests — the guarantee against transition bugs."""

import pytest


from src.domain.entities.task import Task
from src.domain.entities.goal import Goal
from src.domain.errors.tasks_errors import InvalidTransitionError
from src.domain.value_objects.lifecycle import Status
from src.domain.value_objects.tasks_vos import TaskResult


def mk_task(status=Status.PENDING):
    t = Task(id="t", name="t", position=0, description="")
    t.status = status
    return t


# ---- Task: every legal transition ----
def test_task_pending_to_running():
    t = mk_task()
    t.start()
    assert t.status == Status.RUNNING and t.attempt == 1


def test_task_running_to_done():
    t = mk_task()
    t.start()
    t.complete(TaskResult.success("ok"))
    assert t.status == Status.DONE and t.result.output == "ok"


def test_task_running_to_failed():
    t = mk_task()
    t.start()
    t.fail("boom")
    assert t.status == Status.FAILED and t.result.failure_reason == "boom"


def test_task_running_reentry_idempotent_start():
    t = mk_task()
    t.start()
    t.start()  # re-pick after crash
    assert t.status == Status.RUNNING and t.attempt == 2


def test_task_failed_to_requeue_preserves_attempts():
    t = mk_task()
    t.start()
    t.fail("x")
    t.requeue()
    assert t.status == Status.PENDING and t.result is None and t.attempt == 1


def test_task_pending_to_skipped():
    t = mk_task()
    t.skip()
    assert t.status == Status.SKIPPED


# ---- Task: every ILLEGAL transition raises ----
@pytest.mark.parametrize(
    "status,action",
    [
        (Status.DONE, "start"),
        (Status.DONE, "complete"),
        (Status.PENDING, "complete"),  # complete requires RUNNING
        (Status.DONE, "requeue"),
        (Status.PENDING, "requeue"),
        (Status.DONE, "skip"),
        (Status.RUNNING, "skip"),
        (Status.SKIPPED, "start"),
    ],
)
def test_task_illegal_transitions_raise(status, action):
    t = mk_task(status)
    with pytest.raises(InvalidTransitionError):
        if action == "complete":
            t.complete(TaskResult.success("x"))
        else:
            getattr(t, action)()


# ---- Goal transitions ----
def test_goal_lifecycle():
    g = Goal(id="g", name="g", position=0, description="")
    g.start()
    assert g.status == Status.RUNNING
    g.complete()
    assert g.status == Status.DONE


def test_goal_illegal_transition_raises():
    g = Goal(id="g", name="g", position=0, description="")
    g.start()
    g.complete()
    with pytest.raises(InvalidTransitionError):
        g.start()  # can't restart a DONE goal


# ---- Task.reopen (human redo of a good result) ----
def test_task_reopen_done_to_pending_clears_result_counts_separately():
    t = mk_task()
    t.start()
    t.complete(TaskResult.success("ok"))
    t.reopen()
    assert t.status == Status.PENDING
    assert t.result is None  # scan will re-select it
    assert t.reopen_count == 1
    assert t.attempt == 1  # redo does NOT eat into the failure/retry budget


@pytest.mark.parametrize("status", [Status.PENDING, Status.RUNNING, Status.FAILED, Status.SKIPPED])
def test_task_reopen_only_from_done(status):
    t = mk_task(status)
    with pytest.raises(InvalidTransitionError):
        t.reopen()


# ---- Task.abandon (tolerant finalize: iteration abandoned by a replan) ----
def test_task_abandon_running_to_skipped():
    t = mk_task()
    t.start()
    t.abandon()
    assert t.status == Status.SKIPPED


@pytest.mark.parametrize("status", [Status.DONE, Status.SKIPPED, Status.FAILED])
def test_task_abandon_never_from_terminal(status):
    t = mk_task(status)
    with pytest.raises(InvalidTransitionError):
        t.abandon()


# ---- Goal.skip guards ----
def mk_goal(tasks=None, status=Status.PENDING):
    g = Goal(id="g", name="g", position=0, description="", tasks=tasks or [])
    g.status = status
    return g


def test_goal_skip_from_pending():
    g = mk_goal()
    g.skip()
    assert g.status == Status.SKIPPED


def test_goal_skip_from_running_requires_all_tasks_terminal():
    live = mk_task()
    live.start()  # RUNNING task inside
    g = mk_goal(tasks=[live], status=Status.RUNNING)
    with pytest.raises(InvalidTransitionError):
        g.skip()  # cannot skip a goal out from under a live task
    live.abandon()
    g.skip()  # all tasks terminal -> the finalize-abandon path may close it
    assert g.status == Status.SKIPPED


@pytest.mark.parametrize("status", [Status.DONE, Status.SKIPPED, Status.FAILED])
def test_goal_skip_never_from_terminal(status):
    g = mk_goal(status=status)
    with pytest.raises(InvalidTransitionError):
        g.skip()


# ---- Goal.reopen ----
def test_goal_reopen_done_to_running():
    g = mk_goal(status=Status.DONE)
    g.reopen()
    assert g.status == Status.RUNNING


def test_goal_reopen_only_from_done():
    g = mk_goal(status=Status.PENDING)
    with pytest.raises(InvalidTransitionError):
        g.reopen()
