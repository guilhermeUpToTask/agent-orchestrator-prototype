"""
praxis_orchestrator/infra/reasoner/openai_reasoner.py — the real Reasoner (OpenAI-compatible).

Implements the purpose-specific domain port on the runtime package's agent loop:

  converse    — system + persisted history replayed as PLAIN user/assistant
                text (never provider transcripts: immune to dangling tool
                calls and provider switches) + the phase prompt. One terminal
                tool: submit_intent_proposal. A plain-text reply keeps discovery
                waiting; a valid submit opens the exact-revision intent gate.
  architect_cycle — submit_cycle_draft with stable keys and dependencies.
  enrich_goal_contract — submit_goal_contract for the head goal only.
  enrich_goal — quarantined compatibility tool for legacy plans.

Handlers RE-VALIDATE everything (provider schema enforcement is never
trusted) and build the domain objects with new_id() and position=index.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Callable, Sequence, TypeVar

import structlog
from pydantic import BaseModel, ValidationError

from praxis_orchestrator.app.ports import (
    PriorPlanningAttempts,
    ReasonerUnavailable,
    RepositoryOrientation,
    RepositoryReader,
)
from praxis_orchestrator.app.observations import (
    ModelUsagePayload,
    ObservationCorrelation,
    ObservationKind,
    ObservationQuality,
    ObservationRepository,
    ObservationSource,
    TelemetryObservation,
)
from praxis_orchestrator.domain.aggregates.planner_orchestrator import Plan
from praxis_orchestrator.domain.entities.capability import Capability
from praxis_orchestrator.domain.entities.goal import Goal
from praxis_orchestrator.domain.entities.execution_contracts import (
    GoalContract,
    VerificationStrategy,
)
from praxis_orchestrator.domain.entities.planning_artifacts import GoalOutline
from praxis_orchestrator.domain.entities.task import Task
from praxis_orchestrator.domain.factories.identity import new_id
from praxis_orchestrator.domain.value_objects.lifecycle import FailureKind
from praxis_orchestrator.domain.ports.reasoner_port import (
    ChatMessage,
    ConversationMode,
    IntentCandidate,
    ReasonerReply,
)
from praxis_orchestrator.app.verification import test_author_path_allowed
from praxis_orchestrator.infra.reasoner.runtime.context import render_plan_context
from praxis_orchestrator.infra.reasoner.runtime.agent_loop import SessionResult, run_tool_session
from praxis_orchestrator.infra.reasoner.runtime.llm_client import LLMClient
from praxis_orchestrator.infra.reasoner.runtime.prompts import (
    TDD_TASK_GRANULARITY_GUIDANCE,
    SYSTEM_PROMPT,
    build_discovery_prompt,
    build_enrich_prompt,
    build_replanning_prompt,
)
from praxis_orchestrator.infra.reasoner.runtime.tools import ToolSpec
from praxis_orchestrator.infra.reasoner.runtime.tool_profiles import (
    REPOSITORY_READ_SCHEMAS,
    ArtifactCollector,
    ReaderSpec,
    ReasoningPurpose,
    build_tool_profile,
    simple_reader,
)

log = structlog.get_logger(__name__)

_ModelT = TypeVar("_ModelT", bound=BaseModel)


def _validate_submission(model_cls: type[_ModelT], data: object, *, context: str) -> _ModelT:
    """The ONE place a submitted tool-call's args are turned into a domain
    DTO. A weak/free-tier model can submit a schema-shaped-but-wrong payload
    (e.g. a markdown bullet list string where the schema calls for
    list[str]) — `run_tool_session`'s own arg validation catches malformed
    JSON/missing-required-field shapes and lets the model retry within the
    turn budget, but a value that parses fine yet fails a stricter Pydantic
    type (a str where a list is expected) reaches here uncaught otherwise,
    escaping as a raw, unhandled exception all the way to the API (found via
    a real walkthrough against nvidia/nemotron-3-ultra-550b-a55b:free).
    Re-raised as `ReasonerUnavailable(transient=True)` so it flows through
    the SAME planning-failure/backoff/block machinery every other reasoner
    failure already does, instead of crashing the request."""
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        raise ReasonerUnavailable(
            f"{context}: model submitted a schema-invalid payload: {exc}",
            transient=True,
            kind=FailureKind.TOOL_ERROR,
        ) from exc


MAX_HISTORY_MESSAGES = 30  # context-growth cap: replay only the recent tail

# How many unknown-capability rejections a session absorbs before the submit
# is accepted with the unknown ids FILTERED (logged) instead of rejected —
# a stubborn model must not burn the whole turn budget on one bad id.
MAX_CAPABILITY_REJECTIONS = 2

# Turns withheld from the enrichment profile's READ tools: one first submission
# plus one repair after a rejection. Without a reserve the readers can consume the
# whole budget and the contract is never attempted at all.
RESERVED_SUBMIT_TURNS = 2

_TASK_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "imperative task name"},
        "description": {
            "type": "string",
            "description": "precise, executable-without-questions description",
        },
        "required_capabilities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "capability ids from the catalog (optional)",
        },
        "verification_strategy": {
            "type": "string",
            "enum": [item.value for item in VerificationStrategy],
        },
    },
    "required": ["name", "description"],
}

SUBMIT_TASKS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"tasks": {"type": "array", "minItems": 1, "items": _TASK_ITEM_SCHEMA}},
    "required": ["tasks"],
}

SUBMIT_CYCLE_DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "goals": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "name": {"type": "string"},
                    "objective": {"type": "string"},
                    "position": {"type": "integer", "minimum": 0},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["key", "name", "objective", "position", "depends_on"],
            },
        }
    },
    "required": ["goals"],
}

SUBMIT_INTENT_PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "normalized_brief": {"type": "string"},
        "objective": {"type": "string"},
        "scope": {"type": "array", "items": {"type": "string"}},
        "constraints": {"type": "array", "items": {"type": "string"}},
        "exclusions": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "unresolved_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "normalized_brief",
        "objective",
        "scope",
        "constraints",
        "exclusions",
        "assumptions",
        "unresolved_questions",
    ],
}

_CRITERION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "description": {"type": "string"},
    },
    "required": ["id", "description"],
}

SUBMIT_GOAL_CONTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "objective": {"type": "string"},
        "acceptance_criteria": {
            "type": "array",
            "minItems": 1,
            "items": _CRITERION_SCHEMA,
        },
        "tasks": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "objective": {"type": "string"},
                    "acceptance_criteria": {
                        "type": "array",
                        "minItems": 1,
                        "items": _CRITERION_SCHEMA,
                    },
                    "goal_criterion_ids": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                        "description": (
                            "ids taken from this goal's top-level "
                            "acceptance_criteria[].id that THIS task verifies. "
                            "Across all tasks, every goal acceptance criterion id "
                            "must appear at least once — an uncovered criterion is "
                            "rejected. Never invent an id."
                        ),
                    },
                    "allowed_scope": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                        "description": (
                            "Repository-relative PATH prefixes this task may change, "
                            "e.g. 'src/happy_path/greeter.py' or 'backend/praxis_orchestrator/api/'. "
                            "Every file the agent writes must sit under one of them, "
                            "so anything outside is rejected as out of scope. These "
                            "are paths in the repository — NOT capability ids, NOT "
                            "stage names like 'implementation' or 'test_authoring', "
                            "and NOT prose. Mark a directory with a trailing '/'."
                        ),
                    },
                    "forbidden_scope": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional repository-relative PATH prefixes this task must "
                            "not change. Same path rules as allowed_scope."
                        ),
                    },
                    "verification_commands": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    },
                    "verification_strategy": {
                        "type": "string",
                        "enum": [item.value for item in VerificationStrategy],
                    },
                    "required_capabilities": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "objective",
                    "acceptance_criteria",
                    "goal_criterion_ids",
                    "allowed_scope",
                    "verification_commands",
                    "verification_strategy",
                ],
            },
        },
        "cross_task_integration_criterion_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "OPTIONAL — omit or leave empty unless needed. Goal criterion ids "
                "that can only be verified once several tasks are integrated. Any id "
                "listed here MUST also appear in the LAST task's goal_criterion_ids, "
                "because the final task is the integration task that verifies them."
            ),
        },
        "required_capabilities": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["objective", "acceptance_criteria", "tasks"],
}


def _rejected(errors: list[str]) -> str:
    return json.dumps({"accepted": False, "errors": errors})


def _accepted() -> str:
    return json.dumps({"accepted": True})


_PATH_TOKEN = re.compile(r"[\w./\\-]*[/.][\w./\\-]*")


def _command_path_tokens(command: str) -> list[str]:
    """The filesystem-looking arguments of a verification command.

    Deliberately loose: anything containing a `/` or a `.` and no shell
    metacharacters. A false positive costs one rejection the model can argue
    with; a false negative costs a frozen contract that no agent can satisfy.
    """
    tokens: list[str] = []
    for raw in command.split():
        token = raw.strip("\"'")
        if token.startswith("-") or "=" in token or any(ch in token for ch in "*$|><&"):
            continue
        if not _PATH_TOKEN.fullmatch(token):
            continue
        if "/" not in token and "." not in token:
            continue
        # bare version-ish or module-ish tokens ("3.11", "pytest.ini" is fine)
        if token.replace(".", "").isdigit():
            continue
        tokens.append(token)
    return tokens


def _nearest_paths(
    missing: str, candidates: Sequence[str], limit: int = 3, cutoff: float = 0.5
) -> list[str]:
    """Repository paths a missing one was probably meant to be.

    The live failure was `tests/test_greet.py` against a repository containing
    `tests/test_greeter.py`. Naming the near miss is the difference between a
    rejection the model can act on and one it argues with.

    `cutoff` matters more than it looks: files in one directory share a long
    prefix (`tests/test_`), so a loose threshold calls every sibling a near
    miss. Callers that turn a match into a REJECTION pass a strict cutoff;
    callers that only offer a hint can afford a loose one.
    """
    import difflib

    same_dir = [path for path in candidates if path.rsplit("/", 1)[0] == missing.rsplit("/", 1)[0]]
    pool = same_dir or list(candidates)
    return difflib.get_close_matches(missing, pool, n=limit, cutoff=cutoff)


def _validate_scope_paths(
    task_raw: dict[str, Any], path: str, known_caps: set[str]
) -> list[str]:
    """Reject a scope entry that is a CAPABILITY id rather than a repository path.

    `allowed_scope`/`forbidden_scope` are path prefixes: `_matches_scope` compares
    them against repository-relative paths, so an entry that can never match makes
    the frozen contract unsatisfiable — every changed file reports `path outside
    allowed scope`, both policy attempts fail, and the goal blocks with
    execution_failure. No retry can clear it and no `edit_task` can reach the field.

    Observed live: the model copied `required_capabilities` (`implementation`,
    `test_authoring`) straight into `allowed_scope`, twice, across a full replan.
    That confusion is the check: an entry equal to a catalog capability id is a
    category error. A trailing '/' marks a genuine directory of that name and is
    accepted, so a project that really has one stays expressible.
    """
    errors: list[str] = []
    for field in ("allowed_scope", "forbidden_scope"):
        entries = task_raw.get(field)
        if not isinstance(entries, list):
            continue
        offenders = [
            entry
            for entry in entries
            if isinstance(entry, str) and entry.strip() in known_caps
        ]
        if offenders:
            errors.append(
                f"{path}.{field}: {sorted(offenders)} are capability ids, not paths. "
                "This field lists repository-relative PATH prefixes the task may "
                "change (e.g. 'src/happy_path/greeter.py', 'backend/praxis_orchestrator/api/'); "
                "capability ids belong in required_capabilities. If a directory of "
                "that exact name really exists, write it with a trailing '/'."
            )
    return errors


# How many times a rejected PAYLOAD is replayed at the same fingerprint before
# only its reasons are. Payloads anchor: past a couple of attempts, handing back
# the same wrong draft keeps the model repairing its own bad idea instead of
# reconsidering it. Reasons do not anchor, so they keep going.
MAX_PAYLOAD_REPLAYS = 2

# Extra turns granted per prior attempt at the same artifact. A retry that starts
# from a rejected draft plus known pitfalls needs fewer READ turns, but it still
# has to be guaranteed its submission AND its repair — and the earlier failure is
# evidence this particular goal is hard. Capped, because unbounded growth lets one
# goal spend a whole provider budget.
ENRICH_TURNS_PER_PRIOR_ATTEMPT = 2
MAX_ENRICH_TURN_ESCALATION = 6

# Outcomes that describe an ORCHESTRATION race rather than a model mistake.
# Replaying one labelled "REJECTED" teaches a lie: the payload may have been
# perfectly good and was never actually refused.
_NON_INSTRUCTIVE_OUTCOMES = frozenset({"abandoned"})


def _enrichment_fingerprint(plan: Plan, goal: Goal, known_caps: set[str]) -> str:
    """What this enrichment attempt is derived from.

    A replay whose fingerprint no longer matches is discarded silently. That is
    the hard gate: after an `edit_task`, a replan, or a capability-catalog
    change, the prior attempt does not merely fail to help — it describes work
    the model is no longer being asked to do.
    """
    import hashlib

    cycle = next((c for c in plan.cycles if c.status.value == "active"), None)
    material = "|".join(
        [
            goal.id,
            goal.name,
            goal.description or "",
            str(goal.position),
            ",".join(sorted(goal.depends_on)),
            # The active cycle is what carries replan identity: `activate_cycle`
            # mints a new id per goal, so a superseded cycle's rejections are
            # already unreachable, and this makes that true even if an id repeated.
            cycle.id if cycle else "",
            # Empty in practice today — `activate_cycle` clears `intent_proposal`
            # before enrichment ever runs. Kept because it becomes the real intent
            # revision once the cycle retains its approved intent.
            str(plan.intent_proposal.revision if plan.intent_proposal else ""),
            ",".join(sorted(known_caps)),
        ]
    )
    return hashlib.sha256(material.encode()).hexdigest()[:32]


def _architecture_fingerprint(plan: Plan) -> str:
    """What a cycle-architecture attempt is derived from.

    The approved intent revision is the whole input here: revise the intent and
    the previous draft describes a cycle nobody asked for.
    """
    import hashlib

    proposal = plan.intent_proposal
    material = "|".join(
        [
            plan.id,
            plan.brief or "",
            proposal.id if proposal else "",
            str(proposal.revision if proposal else ""),
            str(len(plan.cycles)),
        ]
    )
    return hashlib.sha256(material.encode()).hexdigest()[:32]


def _situation_brief(
    plan: Plan,
    goal: Goal,
    orientation: RepositoryOrientation | None,
    tracked_paths: Sequence[str],
) -> str:
    """What is being contracted, stated inline — not only via read tools.

    The enrichment prompt used to name neither the goal, the brief, nor the
    intent: every fact had to be fetched, and `reserved_submit_turns` guarantees
    a submission but never guarantees the model read anything first. Observed
    live, a session spent its entire budget on readers and submitted nothing.
    Tools are now depth; this is the floor.
    """
    lines = [
        "## What you are contracting",
        f"**Plan brief**: {plan.brief}",
        f"**Goal**: {goal.name} (position {goal.position})",
    ]
    if goal.description:
        lines.append(f"**Goal objective**: {goal.description}")
    if goal.depends_on:
        lines.append(f"**Depends on goals**: {list(goal.depends_on)}")

    cycle = next((c for c in plan.cycles if c.status.value == "active"), None)
    proposal = plan.intent_proposal or (cycle.approved_intent if cycle else None)
    if proposal is not None:
        lines += [
            "",
            "## Approved intent",
            f"**Objective**: {proposal.objective}",
            f"**Scope**: {list(proposal.scope)}",
            f"**Constraints**: {list(proposal.constraints)}",
            f"**Exclusions**: {list(proposal.exclusions)}",
        ]

    lines += ["", "## Repository"]
    if orientation is None:
        lines.append(
            "Inspection is UNAVAILABLE for this project. Do not invent file paths or "
            "test filenames — keep allowed_scope and verification_commands to what the "
            "goal and intent state explicitly."
        )
    else:
        lines += [
            f"**Default branch**: {orientation.default_branch}",
            f"**Top level**: {list(orientation.top_level_entries)}",
            f"**Test directories**: {list(orientation.test_directories)}",
            f"**Conventional test command**: {orientation.detected_test_command or 'unknown'}",
            f"**Tracked files**: {len(tracked_paths)}",
            "",
            "Confirm every path with list_repository_paths / read_repository_file before "
            "putting it in allowed_scope or a verification command. A path that matches "
            "nothing is rejected, and a frozen contract cannot be edited afterwards.",
        ]
    return "\n".join(lines)


def _validate_strategy_scope_agreement(task_raw: dict[str, Any], path: str) -> list[str]:
    """A strategy must not contradict the scope it is frozen with.

    `tdd` and `characterization` both begin with a TEST-AUTHORING stage that
    writes executable checks and is forbidden from touching production files.
    If `allowed_scope` names no path that stage may write, the contract is
    unsatisfiable by ANY agent: every test file is out of scope, and the only
    in-scope files are production.

    Observed live (Tier 1): a contract froze `tdd` with
    `allowed_scope: ["src/happy_path/greeter.py"]`. Both attempts died
    `test author modified production paths` and the goal blocked. Needs no
    repository sight — this is a contradiction inside the submission itself.
    """
    raw_strategy = task_raw.get("verification_strategy")
    try:
        strategy = VerificationStrategy(raw_strategy)
    except ValueError:
        return []  # the schema enum reports a bad value; not this check's job
    if strategy == VerificationStrategy.EXECUTABLE_CHECK:
        return []  # no RED stage: nothing to author, nothing to contradict
    entries = [entry for entry in task_raw.get("allowed_scope") or [] if isinstance(entry, str)]
    if not entries or any(test_author_path_allowed(entry, strategy) for entry in entries):
        return []
    return [
        f"{path}: verification_strategy '{strategy.value}' starts with a test-authoring stage, "
        f"but allowed_scope {entries} contains no path that stage may write. The test author "
        "may only touch tests/ , test_* files, or conftest.py, so this contract cannot be "
        "satisfied by any agent. Add the test path to allowed_scope, or use "
        "'executable_check' if this task authors no tests."
    ]


def _validate_contract_satisfiable(
    task_raw: dict[str, Any],
    path: str,
    tracked_paths: Sequence[str],
) -> list[str]:
    """Reject a contract nothing in the repository could satisfy.

    A frozen `allowed_scope` that matches no path makes EVERY candidate fail
    `path outside allowed scope`; a `verification_command` naming a file that
    does not exist fails on every attempt. Both were produced live, both froze,
    and neither is reachable by `edit_task` afterwards — the goal simply blocks.

    Checked at SUBMISSION time, not at freeze time, and that ordering is the
    whole point: here the model still has the repair turn `RESERVED_SUBMIT_TURNS`
    reserves, whereas at freeze time the only thing left to do with a bad
    contract is open a human-gated block.

    A path that does not exist YET is legitimate — a task creating a new module
    is the normal case — so the rule is that its parent directory must exist.
    """
    if not tracked_paths:
        return []  # no repository sight: cannot judge, so do not invent a verdict
    errors: list[str] = []
    directories = {path.rsplit("/", 1)[0] for path in tracked_paths if "/" in path}
    known = set(tracked_paths)

    def resolvable(candidate: str) -> bool:
        normalized = candidate.strip("./").rstrip("/")
        if not normalized or normalized == ".":
            return True
        if normalized in known or normalized in directories:
            return True
        if any(entry.startswith(f"{normalized}/") for entry in known):
            return True
        parent = normalized.rsplit("/", 1)[0] if "/" in normalized else ""
        return parent == "" or parent in directories

    for entry in task_raw.get("allowed_scope") or []:
        if isinstance(entry, str) and not resolvable(entry):
            suggestion = _nearest_paths(entry.strip("./"), tracked_paths)
            hint = f" Did you mean {suggestion}?" if suggestion else ""
            errors.append(
                f"{path}.allowed_scope: '{entry}' matches nothing in the repository and its "
                f"parent directory does not exist, so every changed file would be rejected as "
                f"out of scope.{hint}"
            )

    for command in task_raw.get("verification_commands") or []:
        if not isinstance(command, str):
            continue
        for token in _command_path_tokens(command):
            normalized = token.strip("./")
            if normalized in known or normalized in directories:
                continue
            # A command may legitimately name a file the task has not created
            # yet — the TDD RED stage authors its own test file. So a missing
            # path alone proves nothing. What DOES prove a mistake is a missing
            # path with a near-twin that exists: a genuinely new file has no
            # close match, while `tests/test_greet.py` next to
            # `tests/test_greeter.py` is a typo every time.
            # Strict: a match here REJECTS the submission, and a false positive
            # would block a task from authoring a legitimately new test file.
            suggestion = _nearest_paths(normalized, tracked_paths, cutoff=0.8)
            if not suggestion:
                continue
            errors.append(
                f"{path}.verification_commands: '{command}' names '{token}', which does not "
                f"exist in the repository, and {suggestion} does. Did you mean that?"
            )

    allowed = {str(entry).strip("./").rstrip("/") for entry in task_raw.get("allowed_scope") or []}
    forbidden = {
        str(entry).strip("./").rstrip("/") for entry in task_raw.get("forbidden_scope") or []
    }
    overlap = sorted(allowed & forbidden)
    if overlap:
        errors.append(
            f"{path}: {overlap} appear in BOTH allowed_scope and forbidden_scope; "
            "a task cannot be required to change a path it is forbidden to touch."
        )
    return errors


def _validate_criterion_coverage(args: dict[str, Any]) -> list[str]:
    """Pre-flight the GoalContract's cross-field referential rules INSIDE the tool
    session, so a near-miss comes back to the model as actionable errors it can fix
    on the next turn.

    Without this the rules were enforced only by `GoalContract`'s own
    `model_validator`, which runs AFTER the session has ended: the submission was
    discarded, the model was never told what was wrong, and the next planning
    attempt started fresh against a schema that states none of these rules — so it
    reproduced the same class of mistake until the attempt budget opened a block.
    Observed live: two consecutive enrichment failures on
    `uncovered goal criteria` and `cross-task criteria must be covered by the final
    integration task`.

    These are HARD rejections (bounded only by the turn budget), unlike unknown
    capability ids: a bad covering is always the model's own mistake and always
    fixable from the error text, whereas an unsatisfiable capability set may not be.

    The domain remains the authority. This mirrors it to provide feedback; if the two
    ever drift, the domain still rejects — just without the repair turn.
    """
    errors: list[str] = []
    criteria = args.get("acceptance_criteria")
    tasks = args.get("tasks")
    if not isinstance(criteria, list) or not isinstance(tasks, list):
        return errors  # shape errors are reported elsewhere

    raw_ids = [c.get("id") for c in criteria if isinstance(c, dict)]
    goal_ids: list[str] = [gid for gid in raw_ids if isinstance(gid, str) and gid]
    if len(goal_ids) != len(set(goal_ids)):
        errors.append("acceptance_criteria: 'id' values must be unique")
    if not goal_ids:
        return errors

    mapped: set[str] = set()
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            continue
        ids = task.get("goal_criterion_ids")
        if not isinstance(ids, list):
            continue
        task_ids = {i for i in ids if isinstance(i, str)}
        unknown = sorted(task_ids - set(goal_ids))
        if unknown:
            errors.append(
                f"tasks[{index}].goal_criterion_ids references ids that are not "
                f"acceptance_criteria ids: {unknown}. Valid ids: {sorted(goal_ids)}"
            )
        mapped |= task_ids

    uncovered = sorted(set(goal_ids) - mapped)
    if uncovered:
        errors.append(
            f"every acceptance criterion must be covered by at least one task, but "
            f"{uncovered} are not referenced by any task's goal_criterion_ids. Add "
            f"each of them to the goal_criterion_ids of whichever task verifies it."
        )

    integration = args.get("cross_task_integration_criterion_ids")
    if isinstance(integration, list) and integration:
        integration_ids = {i for i in integration if isinstance(i, str)}
        unknown = sorted(integration_ids - set(goal_ids))
        if unknown:
            errors.append(
                f"cross_task_integration_criterion_ids references ids that are not "
                f"acceptance_criteria ids: {unknown}"
            )
        final = tasks[-1] if isinstance(tasks[-1], dict) else {}
        final_ids = final.get("goal_criterion_ids")
        final_set = (
            {i for i in final_ids if isinstance(i, str)} if isinstance(final_ids, list) else set()
        )
        missing_in_final = sorted((integration_ids & set(goal_ids)) - final_set)
        if missing_in_final:
            errors.append(
                f"cross_task_integration_criterion_ids {missing_in_final} must ALSO "
                f"appear in the LAST task's goal_criterion_ids (the integration "
                f"task verifies them). Either add them there, or leave "
                f"cross_task_integration_criterion_ids empty — it is optional."
            )
    return errors


def _validate_task_item(item: Any, where: str, known_caps: set[str]) -> list[str]:
    errors: list[str] = []
    if not isinstance(item, dict):
        return [f"{where}: each task must be an object"]
    if not isinstance(item.get("name"), str) or not item["name"].strip():
        errors.append(f"{where}: task 'name' must be a non-empty string")
    if not isinstance(item.get("description"), str):
        errors.append(f"{where}: task 'description' must be a string")
    errors.extend(_validate_tdd_task_granularity(item, where))
    errors.extend(_validate_capability_ids(item, where, known_caps))
    return errors


def _validate_capability_ids(item: Any, where: str, known_caps: set[str]) -> list[str]:
    """Reject capability ids that are not in the catalog.

    Shared by the legacy `submit_tasks` path and the current
    `submit_goal_contract` one. The provider's JSON schema types
    required_capabilities as "array of string" and cannot constrain it to the
    catalog, so this is the only enforcement point -- never trust provider
    schema enforcement (CLAUDE.md).
    """
    if not isinstance(item, dict):
        return []
    caps = item.get("required_capabilities", [])
    if caps in (None, []):
        return []
    if not isinstance(caps, list) or not all(isinstance(c, str) for c in caps):
        return [f"{where}: 'required_capabilities' must be a list of strings"]
    unknown = [c for c in caps if c not in known_caps]
    if not unknown:
        return []
    return [
        f"{where}: unknown capability id(s) {unknown} — use only ids "
        f"from the catalog {sorted(known_caps)} (or omit required_capabilities)"
    ]


def _filter_capability_ids(item: Any, known_caps: set[str], where: str) -> None:
    """Drop uncatalogued capability ids in place, after the rejection budget."""
    if not isinstance(item, dict):
        return
    caps = item.get("required_capabilities")
    if not isinstance(caps, list):
        return
    kept = [c for c in caps if isinstance(c, str) and c in known_caps]
    if kept != caps:
        log.warning(
            "reasoner.unknown_capabilities_filtered",
            where=where,
            dropped=[c for c in caps if c not in kept],
        )
        item["required_capabilities"] = kept


def _validate_tdd_task_granularity(item: dict[str, Any], where: str) -> list[str]:
    if item.get("verification_strategy") != VerificationStrategy.TDD.value:
        return []
    candidates = [item.get("name"), item.get("objective")]
    texts = [value.strip().lower() for value in candidates if isinstance(value, str)]
    write_tests_only = any(
        "write failing test" in text or "write tests for" in text for text in texts
    )
    make_tests_pass_only = any(
        re.fullmatch(r"make(?:\s+\S+){0,8}\s+tests?\s+pass[.!]?", text) for text in texts
    )
    if not (write_tests_only or make_tests_pass_only):
        return []
    return [
        f"{where}: TDD tasks must be feature-level deliverable slices; the "
        "runtime performs the red/green split per task. Do not submit a "
        "write-tests-only or make-tests-pass-only task; state the validated "
        "feature delivered instead."
    ]


def _build_task(item: dict[str, Any], position: int, known_caps: set[str]) -> Task:
    caps_raw = item.get("required_capabilities", [])
    caps = [c for c in caps_raw if isinstance(c, str)] if isinstance(caps_raw, list) else []
    kept = [c for c in caps if c in known_caps]
    if kept != caps:
        log.warning(
            "reasoner.unknown_capabilities_filtered",
            task=item.get("name"),
            dropped=[c for c in caps if c not in known_caps],
        )
    return Task(
        id=new_id(),
        name=str(item["name"]).strip(),
        position=position,
        description=str(item.get("description", "")),
        required_capabilities=kept,
    )


class OpenAIReasoner:
    def __init__(
        self,
        client: LLMClient,
        capabilities: Sequence[Capability] | None = None,
        *,
        converse_max_turns: int = 8,
        enrich_max_turns: int = 4,
        observation_repository: ObservationRepository | None = None,
        provider: str | None = None,
        repository_reader: RepositoryReader | None = None,
        prior_attempts: PriorPlanningAttempts | None = None,
    ) -> None:
        self._client = client
        self._default_caps = list(capabilities or [])
        self._converse_max_turns = converse_max_turns
        self._enrich_max_turns = enrich_max_turns
        self._observation_repository = observation_repository
        self._provider = provider
        # None means no repository sight: the profile is built without the
        # repository readers and the prompt says inspection is unavailable.
        # Stub/dry-run never has one.
        self._repository_reader = repository_reader
        # Read-only: what earlier attempts at this artifact already established.
        # Narrow port on purpose — this adapter must never persist.
        self._prior_attempts = prior_attempts

    def _repository_profile(
        self, project_id: str | None
    ) -> tuple[dict[str, ReaderSpec], RepositoryOrientation | None, list[str]]:
        """Repository readers, orientation, and the tracked path list.

        Resolution happens HERE — once, before the tool loop — never inside a
        tool handler: resolving a project may clone a remote repository, and
        `execute_tool_call` swallows handler exceptions into an opaque
        `{"error": ...}`, so an unbounded network call in there would be
        invisible. A failure degrades to no sight rather than killing planning.
        """
        if self._repository_reader is None or project_id is None:
            return {}, None, []
        reader = self._repository_reader
        try:
            orientation = reader.orientation(project_id)
            tracked = reader.list_paths(project_id, max_entries=2000)
        except Exception as exc:  # noqa: BLE001 — sight is best-effort
            log.warning("reasoner.repository_unavailable", project_id=project_id, error=str(exc))
            return {}, None, []

        def _guard(fn: Callable[[dict[str, Any]], object]) -> Callable[[dict[str, Any]], str]:
            def handle(args: dict[str, Any]) -> str:
                try:
                    return json.dumps(fn(args))
                except Exception as exc:  # noqa: BLE001
                    # An error the model can act on ("not found") beats a
                    # traceback it cannot see.
                    return json.dumps({"error": str(exc)[:300]})

            return handle

        readers = {
            "list_repository_paths": ReaderSpec(
                description=(
                    "List tracked repository file paths, optionally under a directory prefix. "
                    "Use this to confirm a path BEFORE putting it in allowed_scope."
                ),
                input_schema=REPOSITORY_READ_SCHEMAS["list_repository_paths"],
                handler=_guard(
                    lambda args: reader.list_paths(project_id, prefix=str(args.get("prefix") or ""))
                ),
            ),
            "read_repository_file": ReaderSpec(
                description=(
                    "Read one tracked file's contents (truncated). Use this to see the real "
                    "signatures, imports and test names a contract must be written against."
                ),
                input_schema=REPOSITORY_READ_SCHEMAS["read_repository_file"],
                handler=_guard(lambda args: reader.read_file(project_id, str(args.get("path", "")))),
            ),
            "search_repository": ReaderSpec(
                description=(
                    "Find literal text in tracked files; returns path, line number and the line. "
                    "Use it to locate a symbol instead of guessing which file defines it."
                ),
                input_schema=REPOSITORY_READ_SCHEMAS["search_repository"],
                handler=_guard(
                    lambda args: [
                        {"path": hit.path, "line": hit.line, "text": hit.text}
                        for hit in reader.search(
                            project_id,
                            str(args.get("pattern", "")),
                            path_prefix=str(args.get("path_prefix") or ""),
                        )
                    ]
                ),
            ),
        }
        return readers, orientation, tracked

    def _repository_context_reader(self, orientation: RepositoryOrientation | None) -> ReaderSpec:
        """`read_repository_context` — orientation in one call.

        It used to return `{"availability": "adapter_context_only"}`, which is
        worse than nothing: it tells the model repository inspection is
        unavailable, which is precisely the licence to invent a path.
        """
        payload: dict[str, Any]
        if orientation is None:
            payload = {
                "availability": "unavailable",
                "guidance": (
                    "Repository inspection is unavailable for this project. Do not invent "
                    "file paths or test filenames; keep allowed_scope and verification "
                    "commands to what the goal and intent state explicitly."
                ),
            }
        else:
            payload = {
                "availability": "available",
                "default_branch": orientation.default_branch,
                "top_level_entries": list(orientation.top_level_entries),
                "test_directories": list(orientation.test_directories),
                "detected_test_command": orientation.detected_test_command,
                "config_files": list(orientation.config_files),
            }
        return simple_reader(
            "Orientation: default branch, top-level layout, test directories, and the "
            "conventional test command for this repository.",
            lambda: json.dumps(payload),
        )

    def _prior_attempt_count(self, plan_id: str, goal_id: str, fingerprint: str) -> int:
        """Prior attempts at THIS artifact with these inputs."""
        if self._prior_attempts is None:
            return 0
        try:
            history = self._prior_attempts.latest(
                plan_id, "goal_contract", goal_id=goal_id, limit=5
            )
        except Exception:  # noqa: BLE001
            return 0
        return sum(1 for item in history if item.input_fingerprint == fingerprint)

    def _prior_attempt_replay(
        self, plan_id: str, goal_id: str, fingerprint: str
    ) -> str | None:
        """What a previous attempt already established, as a labelled message.

        Every rule here exists to stop a replay doing harm:

        * FINGERPRINT — an attempt derived from different inputs is discarded
          silently, not shown. This is what keeps a replay from surviving an
          edit or a replan, where it would describe work no longer asked for.
        * PAYLOAD CAP — after `MAX_PAYLOAD_REPLAYS` rejections at the same
          fingerprint, only the accumulated reasons are replayed. A payload
          anchors; keep handing it back and the model repairs its own bad idea
          instead of reconsidering it.
        * OUTCOME FILTER — an attempt abandoned by an orchestration race was
          never actually refused, so labelling it "REJECTED" teaches a lie.

        The payload is always labelled REJECTED and never as a starting point.
        """
        if self._prior_attempts is None:
            return None
        try:
            history = self._prior_attempts.latest(
                plan_id, "goal_contract", goal_id=goal_id, limit=5
            )
        except Exception as exc:  # noqa: BLE001 — memory is an optimisation, never a gate
            log.warning("reasoner.prior_attempts_unavailable", plan_id=plan_id, error=str(exc))
            return None

        usable = [
            item
            for item in history
            if item.input_fingerprint == fingerprint
            and item.outcome not in _NON_INSTRUCTIVE_OUTCOMES
            and (item.rejection_reasons or item.payload)
        ]
        if not usable:
            return None

        reasons: list[str] = []
        for item in usable:
            for reason in item.rejection_reasons:
                if reason not in reasons:
                    reasons.append(reason)

        sections = [
            "## Previous attempts at this goal contract",
            f"{len(usable)} earlier submission(s) were REJECTED. Do not repeat them.",
        ]
        if reasons:
            sections.append("Known pitfalls, from those rejections:")
            sections.extend(f"- {reason}" for reason in reasons[:12])

        newest = usable[0]
        if len(usable) <= MAX_PAYLOAD_REPLAYS and newest.payload is not None:
            sections += [
                "",
                "The most recent REJECTED submission (fix it; do not resubmit as-is):",
                json.dumps(newest.payload)[:4000],
            ]
        else:
            sections.append(
                "The earlier drafts are deliberately not shown: repairing them has "
                "already failed. Build a fresh contract that avoids the pitfalls above."
            )
        return "\n".join(sections)

    async def _emit_usage(self, plan: Plan, mode: str, result: SessionResult) -> None:
        """Persist provider usage without fabricating absent token counts.

        Observation failure is isolated from planning: passive telemetry can be
        lost, but it cannot change a domain transition or reasoner result.
        """
        if self._observation_repository is None:
            return
        input_tokens = result.usage.get("prompt_tokens")
        output_tokens = result.usage.get("completion_tokens")
        reasoning_tokens = result.usage.get("reasoning_tokens")
        cached_tokens = result.usage.get("cached_tokens")
        total_tokens = result.usage.get("total_tokens")
        reported = any(
            value is not None
            for value in (
                input_tokens,
                output_tokens,
                reasoning_tokens,
                cached_tokens,
                total_tokens,
            )
        )
        observation = TelemetryObservation(
            correlation=ObservationCorrelation(plan_id=plan.id),
            observed_at=datetime.now(timezone.utc),
            source=ObservationSource.PROVIDER,
            quality=(ObservationQuality.REPORTED if reported else ObservationQuality.UNAVAILABLE),
            kind=ObservationKind.MODEL_USAGE,
            payload=ModelUsagePayload(
                model_request_count=result.llm_calls,
                turn_count=result.turns,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                cached_tokens=cached_tokens,
                total_tokens=total_tokens,
                model=getattr(self._client, "model", None),
                provider=self._provider,
                context=mode,
                phase=plan.phase.value,
                unavailable_reason=(None if reported else "provider_did_not_report_usage"),
            ),
        )
        try:
            await self._observation_repository.append(observation)
        except Exception:
            log.warning(
                "reasoner.usage_observation_failed",
                observation_id=observation.observation_id,
                exc_info=True,
            )

    # ---- converse -------------------------------------------------------
    async def converse(
        self,
        plan: Plan,
        history: Sequence[ChatMessage],
        message: str,
        mode: ConversationMode,
    ) -> ReasonerReply:
        prompt = (
            build_discovery_prompt(plan) if mode == "discovery" else build_replanning_prompt(plan)
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        # replay persisted history as plain text turns (never provider
        # transcripts) — provider-agnostic and immune to dangling tool calls
        for msg in list(history)[-MAX_HISTORY_MESSAGES:]:
            messages.append({"role": msg.role, "content": msg.content})
        messages.append({"role": "user", "content": message or "(proceed)"})

        collector = ArtifactCollector()
        _, orientation, _ = self._repository_profile(plan.project_id)
        readers = {
            "read_project_spec": simple_reader(
                "The project this plan belongs to.",
                lambda: json.dumps({"project_id": plan.project_id}),
            ),
            "read_project_plan": simple_reader(
                "The plan's brief, cycles, goals and task outcomes so far.",
                lambda: render_plan_context(plan, include_results=True),
            ),
            "read_repository_context": self._repository_context_reader(orientation),
            "read_conversation": simple_reader(
                "The recent conversation history for this plan.",
                lambda: json.dumps(
                    [
                        {"role": item.role, "content": item.content}
                        for item in list(history)[-MAX_HISTORY_MESSAGES:]
                    ]
                ),
            ),
        }

        tools = build_tool_profile(
            ReasoningPurpose.INTENT_DISCOVERY,
            readers,
            SUBMIT_INTENT_PROPOSAL_SCHEMA,
            collector.submit,
        )

        result = await run_tool_session(
            self._client,
            messages,
            tools,
            max_turns=self._converse_max_turns,
            allow_plain_reply=True,
            # Discovery offers readers against one submission tool, so it can be
            # read-starved exactly as enrichment was: a free-tier model spent an
            # entire 20-turn budget reading and never submitted. Reserving the
            # tail WITHDRAWS the readers, so the model reaches turns where
            # submitting is the only tool it has. A clarifying question is still
            # possible on those turns — `allow_plain_reply` handles a no-tool
            # reply, and the reserve constrains reading, not answering.
            reserved_submit_turns=RESERVED_SUBMIT_TURNS,
        )
        await self._emit_usage(plan, mode, result)

        if not result.submitted:
            return ReasonerReply(
                message=result.text,
                model_request_count=result.llm_calls,
                tool_turn_count=result.turns,
            )

        candidate = _validate_submission(
            IntentCandidate, collector.value or result.submit_args, context="submit_intent_proposal"
        )
        if candidate.unresolved_questions:
            return ReasonerReply(
                message=result.text or " ".join(candidate.unresolved_questions),
                model_request_count=result.llm_calls,
                tool_turn_count=result.turns,
            )
        reply_text = result.text or "Intent proposal is ready for your review."
        return ReasonerReply(
            message=reply_text,
            intent=candidate,
            model_request_count=result.llm_calls,
            tool_turn_count=result.turns,
        )

    # ---- enrich_goal ----------------------------------------------------
    async def enrich_goal(
        self,
        plan: Plan,
        goal: Goal,
        capabilities: Sequence[Capability],
    ) -> list[Task]:
        caps = list(capabilities)
        known_caps = {c.id for c in caps}
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_enrich_prompt(plan, goal, caps)},
        ]
        state: dict[str, Any] = {"rejections": 0}

        def handle_submit_tasks(args: dict[str, Any]) -> str:
            tasks_raw = args.get("tasks")
            if not isinstance(tasks_raw, list) or not tasks_raw:
                return _rejected(["'tasks' must be a non-empty array"])
            errors: list[str] = []
            for ti, task_raw in enumerate(tasks_raw):
                errors.extend(_validate_task_item(task_raw, f"tasks[{ti}]", known_caps))
            if errors and _only_capability_errors(errors):
                state["rejections"] += 1
                if state["rejections"] <= MAX_CAPABILITY_REJECTIONS:
                    return _rejected(errors)
                return _accepted()  # final: accept, filter unknown ids on build
            if errors:
                return _rejected(errors)
            return _accepted()

        submit_tasks = ToolSpec(
            name="submit_tasks",
            description=(
                f"Submit the ordered task breakdown for goal '{goal.name}'. Call exactly once."
            ),
            input_schema=SUBMIT_TASKS_SCHEMA,
            handler=handle_submit_tasks,
            terminal=True,
        )

        result = await run_tool_session(
            self._client,
            messages,
            [submit_tasks],
            max_turns=self._enrich_max_turns,
            allow_plain_reply=False,
        )
        await self._emit_usage(plan, "enrich", result)
        return [
            _build_task(task_raw, ti, known_caps)
            for ti, task_raw in enumerate(result.submit_args["tasks"])
            if isinstance(task_raw, dict)
        ]

    async def architect_cycle(self, plan: Plan) -> list[GoalOutline]:
        proposal = plan.intent_proposal
        if proposal is None or proposal.approved_at is None:
            raise ValueError("approved intent is required for cycle architecture")
        collector = ArtifactCollector()
        repository_readers, orientation, _tracked = self._repository_profile(plan.project_id)
        readers = {
            "read_project_spec": simple_reader(
                "The project this plan belongs to.",
                lambda: json.dumps({"project_id": plan.project_id}),
            ),
            "read_project_plan": simple_reader(
                "The plan's brief, cycles, goals and task outcomes so far.",
                lambda: render_plan_context(plan, include_results=True),
            ),
            "read_repository_context": self._repository_context_reader(orientation),
            "read_approved_intent": simple_reader(
                "The approved intent: objective, scope, constraints, exclusions.",
                lambda: proposal.model_dump_json(),
            ),
            "read_prior_evidence": simple_reader(
                "Evidence references from previous cycles.",
                lambda: json.dumps([cycle.evidence_refs for cycle in plan.cycles]),
            ),
            **repository_readers,
        }

        tools = build_tool_profile(
            ReasoningPurpose.CYCLE_ARCHITECTURE,
            readers,
            SUBMIT_CYCLE_DRAFT_SCHEMA,
            collector.submit,
        )
        source_instruction = (
            " This is a replan. Read the project plan and prior evidence before "
            "submitting. Treat DONE goals and tasks as locked history: do not "
            "recreate or redo them. Account only for unfinished source work and "
            "the newly approved intent in the replacement cycle."
            if proposal.source_cycle_id is not None
            else ""
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Generate one ordered CycleDraft for the approved intent. "
                    "Use stable local goal keys and only real dependency keys; "
                    "strict execution order is represented by position, not fake edges. "
                    f"{source_instruction} Submit it with submit_cycle_draft."
                ),
            },
        ]
        try:
            result = await run_tool_session(
                self._client,
                messages,
                tools,
                max_turns=self._converse_max_turns,
                allow_plain_reply=False,
                # Same starvation, same guard: four readers against one
                # submission tool. Without the reserve, architecture depends on
                # the model volunteering to stop reading.
                reserved_submit_turns=RESERVED_SUBMIT_TURNS,
            )
        except ReasonerUnavailable as exc:
            # Same contract as enrichment: the adapter never persists, it attaches
            # what the dead session established and the handler decides.
            exc.input_fingerprint = _architecture_fingerprint(plan)
            raise
        await self._emit_usage(plan, "cycle_architecture", result)
        value = collector.value or result.submit_args
        goals = [
            _validate_submission(GoalOutline, item, context="submit_cycle_draft")
            for item in value.get("goals", [])
        ]
        # Reuse CycleDraft's validator at the application boundary; the adapter
        # returns only candidate DTOs and cannot activate anything.
        return goals

    async def enrich_goal_contract(
        self,
        plan: Plan,
        goal: Goal,
        capabilities: Sequence[Capability],
    ) -> GoalContract:
        proposal = next(
            (cycle for cycle in plan.cycles if cycle.status.value == "active"),
            None,
        )
        collector = ArtifactCollector()
        repository_readers, orientation, tracked_paths = self._repository_profile(plan.project_id)
        readers = {
            "read_project_spec": simple_reader(
                "The project this plan belongs to.",
                lambda: json.dumps({"project_id": plan.project_id}),
            ),
            "read_project_plan": simple_reader(
                "The plan's brief, cycles, goals and task outcomes so far.",
                lambda: render_plan_context(plan, include_results=True),
            ),
            "read_repository_context": self._repository_context_reader(orientation),
            "read_approved_intent": simple_reader(
                "The approved intent: objective, scope, constraints, exclusions.",
                # The real proposal now that the cycle retains it (unfreeze #17).
                # This used to return only an opaque id, so the stage that freezes
                # scope and commands could not see the scope it had to honour.
                lambda: (
                    proposal.approved_intent.model_dump_json()
                    if proposal is not None and proposal.approved_intent is not None
                    else json.dumps(
                        {"intent_proposal_id": proposal.intent_proposal_id if proposal else None}
                    )
                ),
            ),
            "read_active_goal": simple_reader(
                "The goal being contracted: name, objective, position, dependencies.",
                lambda: goal.model_dump_json(),
            ),
            "read_prior_evidence": simple_reader(
                "Evidence references from the active cycle.",
                lambda: json.dumps(proposal.evidence_refs if proposal else []),
            ),
            **repository_readers,
        }

        caps = list(capabilities)
        known_caps = {capability.id for capability in caps}
        state: dict[str, Any] = {"rejections": 0, "last_payload": None, "last_errors": []}

        def handle_submit_goal_contract(args: dict[str, Any]) -> str:
            tasks_raw = args.get("tasks")
            if not isinstance(tasks_raw, list) or not tasks_raw:
                return _rejected(["'tasks' must be a non-empty array"])
            errors: list[str] = []
            capability_errors: list[str] = []
            for ti, task_raw in enumerate(tasks_raw):
                if isinstance(task_raw, dict):
                    errors.extend(_validate_tdd_task_granularity(task_raw, f"tasks[{ti}]"))
                    # A hard rejection, like the coverage rules: a capability id in a
                    # path field is always the model's own mistake and always fixable
                    # from the error text.
                    errors.extend(_validate_scope_paths(task_raw, f"tasks[{ti}]", known_caps))
                    # Repository-grounded: a scope or command that nothing could
                    # satisfy is rejected HERE, where the model still has the
                    # repair turn, instead of freezing and blocking the goal.
                    errors.extend(
                        _validate_contract_satisfiable(task_raw, f"tasks[{ti}]", tracked_paths)
                    )
                    # Needs no repository sight: a strategy that contradicts its
                    # own scope is unsatisfiable on any repository.
                    errors.extend(_validate_strategy_scope_agreement(task_raw, f"tasks[{ti}]"))
                    capability_errors.extend(
                        _validate_capability_ids(task_raw, f"tasks[{ti}]", known_caps)
                    )
            capability_errors.extend(_validate_capability_ids(args, "goal_contract", known_caps))
            errors.extend(_validate_criterion_coverage(args))
            if errors:
                # Keep the last rejected submission: if the session then dies on
                # its turn budget, this is the only trace of the work, and the
                # retry would otherwise rebuild its prompt from nothing.
                state["last_payload"] = args
                state["last_errors"] = errors + capability_errors
                return _rejected(errors + capability_errors)
            if capability_errors:
                # Same budget as the legacy submit_tasks path: give the model a
                # bounded number of chances to pick real catalog ids, then accept
                # and filter on build. An unsatisfiable capability set frozen into
                # a TaskContract hard-blocks the goal at role resolution
                # (agent_capability), which no retry can clear.
                state["rejections"] += 1
                if state["rejections"] <= MAX_CAPABILITY_REJECTIONS:
                    return _rejected(capability_errors)
            return collector.submit(args)

        tools = build_tool_profile(
            ReasoningPurpose.GOAL_ENRICHMENT,
            readers,
            SUBMIT_GOAL_CONTRACT_SCHEMA,
            handle_submit_goal_contract,
        )
        fingerprint = _enrichment_fingerprint(plan, goal, known_caps)
        replay = self._prior_attempt_replay(plan.id, goal.id, fingerprint)
        turn_budget = self._enrich_max_turns + min(
            self._prior_attempt_count(plan.id, goal.id, fingerprint)
            * ENRICH_TURNS_PER_PRIOR_ATTEMPT,
            MAX_ENRICH_TURN_ESCALATION,
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _situation_brief(plan, goal, orientation, tracked_paths)},
            *([{"role": "user", "content": replay}] if replay else []),
            {
                "role": "user",
                "content": (
                    "Freeze a complete GoalContract for the active goal. Every goal "
                    "acceptance criterion id must be covered by at least one task: "
                    "each task lists in goal_criterion_ids the criterion ids it "
                    "verifies, and no criterion may be left unreferenced. Leave "
                    "cross_task_integration_criterion_ids EMPTY unless a criterion "
                    "genuinely requires several tasks integrated first — anything "
                    "listed there must also appear in the LAST task. "
                    "Use TDD for new "
                    "behavior, characterization for preserving behavior, and "
                    "executable_check where RED is meaningless. "
                    f"{TDD_TASK_GRANULARITY_GUIDANCE} "
                    "required_capabilities, where given, must use ONLY these "
                    f"catalog ids: {sorted(known_caps)}. Omit the field if none "
                    "apply — never invent an id. Those ids are NOT scope: "
                    "allowed_scope and forbidden_scope list repository-relative "
                    "PATH prefixes the task may or may not change. Submit exactly once."
                ),
            },
        ]
        try:
            result = await run_tool_session(
                self._client,
                messages,
                tools,
                max_turns=turn_budget,
                # This profile hands the model six readers against one submission.
                # Reserve the tail of the budget so the contract always gets attempted
                # and a rejected one always gets its repair turn.
                reserved_submit_turns=RESERVED_SUBMIT_TURNS,
                allow_plain_reply=False,
            )
        except ReasonerUnavailable as exc:
            # The work the session had done is the only thing worth keeping from a
            # failure. Attach it and let the HANDLER decide whether to record it —
            # the reasoner reads, it never persists.
            exc.partial_artifact = state.get("last_payload")
            exc.rejection_reasons = tuple(state.get("last_errors") or ())
            exc.input_fingerprint = fingerprint
            raise
        await self._emit_usage(plan, "goal_enrichment", result)
        value = dict(collector.value or result.submit_args)
        value["id"] = goal.id
        value["frozen_at"] = datetime.min.replace(tzinfo=timezone.utc)
        _filter_capability_ids(value, known_caps, "goal_contract")
        for position, task in enumerate(value.get("tasks", [])):
            task["id"] = new_id()
            task["position"] = position
            task["revision"] = 1
            _filter_capability_ids(task, known_caps, f"tasks[{position}]")
        return _validate_submission(GoalContract, value, context="submit_goal_contract")


def _only_capability_errors(errors: list[str]) -> bool:
    return all("unknown capability id" in e for e in errors)
