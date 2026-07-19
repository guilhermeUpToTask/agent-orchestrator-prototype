  ## Recommended implementation plan

  ### 1. Fix the beginning of the walkthrough

  - Replace “New plan” with project-aware language: “Open project plan” or “Start project cycle.”
  - When no project exists, show an inline “Create project first” form or direct action—not a disabled selector.
  - Return whether plan creation produced a new plan or opened an existing one. Never silently discard a new brief.
  - Change the submit action to “Create & analyze brief.”
  - Immediately render the submitted brief as a visible card in chat.
  - Start an idempotent discovery operation automatically. The user should not need to ask, “Is my plan good?”
  - The first reasoner response should contain:
      - a short normalized brief;
      - assumptions already safe to make;
      - only the unresolved questions;
      - a visible “waiting for your answers” state.

  For the initial release, the request can remain synchronous, but it must expose started/running/failed state. The follow-up slice should give discovery a durable planning-run record so refreshes and API timeouts do not lose its status.

  ### 2. Make cyclic JIT planning canonical

  Use the accepted lifecycle already described in docs/architecture/plan-lifecycle.md:1:

  Brief → IntentProposal → approval
        → GoalOutline roadmap → approval
        → GoalContract/tasks for goal 1 → execute goal 1
        → GoalContract/tasks for goal 2 → execute goal 2
        → …
        → publication review

  Important rules:

  - Roadmap generation submits only stable goal keys, descriptions, order, and real dependencies.
  - Task generation happens only for the current head goal.
  - Persist the completed GoalContract before execution starts.
  - A crash after goal 2 enrichment resumes at goal 3; it never regenerates goals 1–2.
  - A failed enrichment retries that same goal and cannot advance execution.
  - Keep task generation per goal. Generating tasks one at a time would lose goal-level coherence and create more model calls.
  - Cap a goal at roughly 1–6 tasks. If it needs more, the roadmap should split the goal.

  Retire the legacy “enrich every goal before execution approval” path once migrated clients no longer need it.

  ### 3. Correct provider failure handling

  Introduce a normalized runtime failure carrying:

  - kind;
  - provider/model/runtime;
  - provider error code;
  - retryability;
  - retry_after;
  - limit scope: request concurrency, quota, daily quota, or unknown capacity;
  - exit code and bounded safe message.

  Immediate taxonomy fix:

  - Classify ResourceExhausted, RESOURCE_EXHAUSTED, “request limit reached,” and similar capacity messages as rate_limit or a new provider_capacity subtype.
  - Add the exact NVIDIA examples as regression fixtures.

  Policy changes:

  - Honor provider Retry-After when available.
  - Use exponential backoff with jitter; 2 and 4 seconds are far too short for this failure.
  - Add a persisted provider/model circuit breaker. A global NVIDIA limit should not consume three attempts independently across every task or plan.
  - After the circuit opens, block the current stage and offer explicit actions: wait and retry, switch provider/model, edit the task, or pause.
  - Never run a later task while the head task is backing off or blocked.

  Human intervention should be required only after the retry window/circuit threshold is exhausted—not after the first transient error.

  ### 4. Build truthful planning and execution telemetry

  Extend the current execution ledger and typed observation system rather than creating another event store.

  Record:

  - planning operation purpose and target goal;
  - queued, started, waiting-for-user, committed, failed, backing-off;
  - model request and tool-turn counts;
  - run/attempt ID, start, elapsed, last liveness, timeout, exit, and retry time;
  - bounded stdout/stderr chunks;
  - structured Pi tool, step, usage, and failure events;
  - observation source, quality, and coverage.

  Privacy rules should exclude prompts, source contents, secrets, full environment dictionaries, and absolute worktree paths by default.

  Metrics must distinguish:

  - Planner LLM usage;
  - child-agent LLM usage;
  - combined totals;
  - unavailable versus zero;
  - reported versus estimated usage;
  - coverage percentage.

  ### 5. Replace the console with an attempt timeline

  - Hydrate durable history from the API before tailing SSE.
  - Group entries by task → logical run → concrete attempt.
  - Show severity, provider, duration, retry countdown, failure kind, and recovery action.
  - Add selected-task and failed-only filters.
  - Keep domain lifecycle events in Activity; keep runtime evidence in the attempt view.
  - Rename the current component to “Agent events” until real stdout/tool streaming lands.
  - Display planning progress such as “Generating tasks for goal 2 of 6,” not merely “reasoning…”

  ### 6. Finish recovery and workspace safety

  - Reconcile stale RUNNING attempts after lease expiry or worker restart.
  - Automatically run safe git worktree prune.
  - Audit that failed branches/worktrees are absent and successful commits reached only the intended goal/cycle branch.
  - Preserve the existing rule that pause waits for the active atomic attempt to finalize.
  - UI copy should explicitly say: “Pause requested; current attempt is still running. No new work will start.”

  ### 7. Verification and delivery gates

  Required acceptance tests:

  - Pause during an attempt settles only after finalization.
  - Console history survives refresh and groups attempts correctly.
  Focused verification of the current worktree passed 201 backend tests across orchestration, reasoner, runtime, API, migrations, observations, and Git workspace, plus the frontend production build. This was a plan-only review; no source files were changed. Audit
  time was about eight minutes.