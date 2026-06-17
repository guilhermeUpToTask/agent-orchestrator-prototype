I've traced the full event pipeline. Here's the analysis.

How the events flow

TaskAssignUseCase â publishes task.assigned (creates lease, TTL 300s)
   ââ worker.process â TaskExecuteUseCase.execute:
        publish task.execution_started âââ¶ TaskManager.record_started: ASSIGNEDâIN_PROGRESS
        _prepare_workspace (git clone/fetch)         â lease NOT refreshed yet
        start lease refresher (+120s every 60s)       â only starts HERE
        run agent (pi) â¦ up to task_timeout 600s
        publish execution_succeeded/failed âââ¶ TaskManager.record_*

Reconciler (every 60s): for each non-terminal task â
   is_lease_active(task_id)?  if ASSIGNED/IN_PROGRESS and lease gone â FAIL_LEASE_EXPIRED â task.failed
        ââ TaskFailHandlingUseCase: attempt++ ; if attempt â¥ max_retries(2) â CANCELED
              ââ GoalAggregate.record_task_canceled â goal.FAILED

What's actually killing write-setup-tests

reconciler.fail_lease_expired reason='Lease expired while ASSIGNED' â the lease key vanished while the task was still in flight, so the reconciler failed it. Because it had already accumulated attempts=2 (from the earlier crash-loop episodes), this reclamation
hit max_retries â CANCELED â goal.failed reason=max_retries_exhausted. That's the "requeued again, then nothing, th

The real bugs (design flaws, not just tuning)

1. Lease TTL (300s) < agent timeout (600s). task_assign creates the lease with lease_seconds=300, but task_execute _timeout_seconds=600. A healthy agent that legitimately runs 300â600s will have its lease expire unless everyrefresh lands perfectly â and the refresher only starts after _prepare_workspace (task_execute.py:176), so there's an unprotected window at the start. Any hiccup (slow clone, a worker restart that kills the daemon refresher thread, a transient Redis miss) drops the lease.
2. Lease-expiry is conflated with task failure and burns the retry budget. The reconciler turns "lease gone" into tdlingUseCase treats like a real agent failure: it increments the attempt counter and, at max_retries=2, cancels thetask and fails the whole goal. So a slow/restarting worker â an infra-liveness signal â permanently kills the task and the goal after just two reclaims. Liveness reclamation should requeue the task (hand it back to the scheduler) without consuming the genuine-failure budget.
3. Proximate trigger: the pi agent isn't completing. [pi] no live log streaming â check stdout.txt + the unintended claude-sonnet-4-5 model (empty runtime_config) means pi is running long/hanging, so executions don't finish inside the lease/retry envelope â which is what keeps tripping #1 and #2.