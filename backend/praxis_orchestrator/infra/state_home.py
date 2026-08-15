"""Where durable state lives.

One function, in one place, because two callers need the default and a
disagreement between them would point half the system at a different database:
the composition root (`infra/container.py`) and the CLI runner's fallback for
direct construction.

`PRAXIS_HOME` overrides it. Nothing else does — there is deliberately no
fallback to a pre-rename name or directory. The project has never been
published, so the only installs that predate the rename are the maintainer's
own, and those were migrated rather than accommodated.
"""

from __future__ import annotations

import os
from pathlib import Path

HOME_ENV = "PRAXIS_HOME"
HOME_DIRNAME = ".praxis"


def resolve_home() -> Path:
    """`PRAXIS_HOME` if set, otherwise `~/.praxis`."""
    override = os.environ.get(HOME_ENV)
    return Path(override) if override else Path.home() / HOME_DIRNAME
