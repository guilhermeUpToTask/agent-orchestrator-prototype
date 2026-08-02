"""Render a cycle's accepted evidence into a pull-request title and body.

Pure: takes data, returns strings, touches nothing.

The evidence is the product's actual argument. A reviewer who can see that this
exact command exited 0 against this candidate commit, and that the test lives in
a separate commit that was RED before it was GREEN, reviews differently from one
handed an anonymous diff — so it belongs where a reviewer will actually read it,
which is the pull request, not a console they may never open.
"""

from __future__ import annotations

from dataclasses import dataclass

_TITLE_LIMIT = 72


@dataclass(frozen=True)
class EvidenceLine:
    task_title: str
    command: str
    exit_code: int
    candidate_commit_sha: str
    test_commit_sha: str


def render_pr_title(objective: str, cycle_id: str) -> str:
    first_line = objective.strip().splitlines()[0].strip() if objective.strip() else ""
    if not first_line:
        return f"Cycle {cycle_id[:8]}"
    if len(first_line) <= _TITLE_LIMIT:
        return first_line
    return first_line[: _TITLE_LIMIT - 1].rstrip() + "…"


def render_pr_body(
    *,
    cycle_id: str,
    objective: str,
    evidence: list[EvidenceLine],
    goal_count: int,
) -> str:
    lines: list[str] = []
    if objective.strip():
        lines += [objective.strip(), ""]

    lines += [
        "## Verification evidence",
        "",
        f"{goal_count} goal(s) promoted into `cycle/{cycle_id}`. Every task below "
        "reached DONE with revision-bound evidence; nothing was merged without it.",
        "",
    ]

    if evidence:
        lines += [
            "| Task | Command | Exit | Candidate | Test |",
            "|---|---|---|---|---|",
        ]
        for item in evidence:
            lines.append(
                f"| {item.task_title} | `{item.command}` | {item.exit_code} "
                f"| `{item.candidate_commit_sha[:8]}` | `{item.test_commit_sha[:8]}` |"
            )
    else:
        lines.append("_No accepted verification evidence was recorded for this cycle._")

    lines += [
        "",
        "---",
        "",
        "Opened by the agent orchestrator. It does not merge pull requests — "
        "that decision is yours.",
    ]
    return "\n".join(lines)
