"""Which checks belong to THIS task.

The pipeline needs to answer that before it can freeze a `TestBundle`, and there
are only two honest sources:

- **the author's diff** — the author just wrote these files, so they are this
  task's by construction. Unambiguous, needs nothing from this module.
- **the contract's own declaration** — a `verification_command` naming a concrete
  check file (`pytest -q tests/test_greeter.py::test_greet`). That is what this
  module resolves.

There is deliberately no third source. Scanning the repository for test files was
tried and is wrong: it cannot tell task 3's checks from task 1's, so on a
multi-task goal it freezes another task's failing test as this task's evidence.
Which tests prove a task is done is *intent* — it exists nowhere in the tree and
has to be declared. This is the Requirements Traceability Matrix, and
`TestBundle.criterion_to_tests` is the field that holds it.

The bar for "declared" is deliberately high, because the fallback is safe and the
mistake is not. A concrete FILE counts; a bare `pytest -q` or a directory
(`pytest -q tests/`) does not — a directory selects every task's checks, which is
exactly the collision this module exists to prevent. Anything that does not
clearly name a file means the author writes the check, which can never be wrong:
authoring costs one agent run, mis-identifying costs a false green or a block on
someone else's failing test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from src.app.verification import is_check_path
from src.domain.entities.execution_contracts import TaskContract


@dataclass(frozen=True)
class DeclaredChecks:
    """Concrete checks this task's contract names, resolved against the repo."""

    #: Selectors as written, including any ``::node`` suffix. These become the
    #: values of ``TestBundle.criterion_to_tests``.
    node_ids: list[str] = field(default_factory=list)
    #: The check FILES those selectors live in — the protected set.
    files: list[str] = field(default_factory=list)

    @property
    def declared(self) -> bool:
        """True only when the contract names a concrete check file.

        The caller reads False as "author the checks", never as an error.
        """
        return bool(self.files)


def _candidate_tokens(command: str) -> list[str]:
    """Path-like arguments of a shell command.

    Mirrors ``contract_repair._looks_like_path`` — flags, assignments and
    anything with shell metacharacters are not paths.
    """
    tokens: list[str] = []
    for raw in command.split():
        token = raw.strip("\"'")
        if token.startswith("-") or "=" in token:
            continue
        if any(ch in token for ch in "*$|><&"):
            continue
        if "/" not in token and "." not in token:
            continue
        if token.replace(".", "").isdigit():
            continue
        tokens.append(token)
    return tokens


def declared_checks(contract: TaskContract, root: Path) -> DeclaredChecks:
    """Resolve the checks ``contract`` names against the worktree at ``root``.

    A selector resolves only when its file part exists AND reads as a check
    rather than production code — a command naming `src/greeter.py` is running
    the code, not selecting a check.
    """
    node_ids: list[str] = []
    files: list[str] = []

    for command in contract.verification_commands:
        for token in _candidate_tokens(command):
            selector = token.strip("./")
            # `tests/test_x.py::test_y` -> the file is everything before `::`.
            path = selector.split("::", 1)[0]
            if not is_check_path(path) or not (root / path).is_file():
                continue
            if selector not in node_ids:
                node_ids.append(selector)
            if path not in files:
                files.append(path)

    return DeclaredChecks(node_ids=sorted(node_ids), files=sorted(files))


def criterion_test_map(
    contract: TaskContract, check_selectors: Sequence[str]
) -> dict[str, list[str]]:
    """The criterion -> checks mapping frozen into the ``TestBundle``.

    Until now this was written as ``{every criterion: all changed paths}`` — the
    same list copied under every key, so it carried no information and was read
    only for its key set (`Task.freeze_test_bundle`). Every criterion still maps
    to every selector here, because nothing in the contract says which criterion
    a given test covers; what changed is that the selectors are now *this task's*
    checks rather than whatever the diff happened to contain.

    Per-criterion resolution needs the reasoner to declare it (or per-test
    coverage to derive it) and is deferred; the shape is already correct for it.
    """
    selectors = list(check_selectors)
    return {criterion.id: list(selectors) for criterion in contract.acceptance_criteria}
