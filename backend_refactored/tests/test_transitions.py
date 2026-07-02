"""Exhaustive state-transition tests — the guarantee against transition bugs."""

import sys, os, pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from domain.entities.task import Task
from domain.entities.goal import Goal
from domain.errors.tasks_errors import InvalidTransitionError
from domain.value_objects.tasks_vos import Status, TaskResult


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
