# Refactor Plan Implementation Audit

Date: 2026-03-24
Reference: `docs/deep_code_review_2026-03-24.md`

## Verdict
All planned steps from the refactor plan are now implemented.

## Status by plan step

## P0 — Critical

### P0.1 Fix event ACK strategy in Redis adapter
- **Status:** ✅ Implemented.
- **Evidence:** Redis adapter no longer ACKs in `subscribe_many`; it records pending messages and requires explicit `ack(event, group)` from consumers.
- **Follow-through:** task-manager, worker, and orchestrator loops now ACK after successful handling.

### P0.2 Make PR creation truly idempotent
- **Status:** ✅ Implemented.
- **Evidence:** `CreateGoalPRUseCase.execute()` checks `find_open_pr(...)` before `create_pr(...)`.

### P0.3 Harden task cancellation transitions
- **Status:** ✅ Implemented.
- **Evidence:** `TaskAggregate.cancel()` enforces `_assert_status(...)` and clears assignment before cancellation.

---

## P1 — Structural

### P1.1 Remove infra imports from `ProposeSpecChange`
- **Status:** ✅ Implemented.
- **Evidence:** Proposal path/write responsibilities moved behind `ProjectSpecRepository.proposal_path(...)` + `save_proposal(...)`; app use case no longer imports infra modules directly.

### P1.2 Normalize dependency-unblock semantics
- **Status:** ✅ Implemented.
- **Evidence:** `TaskUnblockUseCase` uses `task.is_assignable()` and `task.is_unblocked(...)`.

### P1.3 Add CAS/versioned save for project-plan transitions
- **Status:** ✅ Implemented.
- **Evidence:** `ProjectPlanRepositoryPort.update_if_version(...)` added and implemented in YAML/in-memory adapters; `_check_phase_completion()` uses CAS retry updates.

---

## P2 — Cleanup / optimization

### P2.1 Consolidate event routing maps
- **Status:** ✅ Implemented.
- **Evidence:** task-manager loop uses a centralized event→handler map; orchestrator dispatch also uses a handler map.

### P2.2 Improve test realism for eventing
- **Status:** ✅ Implemented.
- **Evidence:** Redis event adapter tests now assert explicit-ack behavior and no automatic ACK on yield.

### P2.3 Document orchestration authority boundaries
- **Status:** ✅ Implemented.
- **Evidence:** Added `docs/orchestration_authority_matrix.md` with transition authority and guardrails.

---

## Summary
Implemented: 9 / 9 steps.
No missing steps detected against the original refactor plan.
