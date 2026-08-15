"""
praxis_orchestrator/infra/env_compat.py — reading an install that predates the rename.

The project was renamed to Praxis Orchestrator (Phase 10B). Three of the things
it renamed are not code: they are state and configuration sitting on machines
that already installed it.

  ORCHESTRATOR_MASTER_KEY  -> PRAXIS_MASTER_KEY
  ORCHESTRATOR_API_TOKEN   -> PRAXIS_API_TOKEN
  ORCHESTRATOR_HOME        -> PRAXIS_HOME
  ORCHESTRATOR_DB_URL      -> PRAXIS_DB_URL
  ~/.orchestrator          -> ~/.praxis

**The rule: an existing install keeps working with no operator action, and must
never silently lose state or drop auth.** The API token is the one that makes
this non-negotiable rather than polite — `api/security.py` treats an unset token
as "open in local dev", so an upgrade that stopped reading the old name would
not error. It would silently unguard the control plane.

Legacy names are honoured for at least one minor release, and each is warned
about ONCE per process rather than per read.
"""

from __future__ import annotations

import os
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

# new name -> the pre-rename name it replaced
_ALIASES = {
    "PRAXIS_HOME": "ORCHESTRATOR_HOME",
    "PRAXIS_MASTER_KEY": "ORCHESTRATOR_MASTER_KEY",
    "PRAXIS_API_TOKEN": "ORCHESTRATOR_API_TOKEN",
    "PRAXIS_DB_URL": "ORCHESTRATOR_DB_URL",
}
# Deliberately NOT here: ORCHESTRATOR_EMBED_COORDINATORS. It belonged to the
# pre-refactor architecture (coordinator threads inside the API process) and
# nothing has read it for two rewrites. An alias for a variable no code reads
# is a promise of compatibility that cannot be kept or tested.

_warned: set[str] = set()

CURRENT_HOME_DIRNAME = ".praxis"
LEGACY_HOME_DIRNAME = ".orchestrator"


def env(name: str, default: str | None = None) -> str | None:
    """Read `name`, falling back to the pre-rename variable it replaced.

    The new name always wins when both are set, so an operator midway through
    migrating gets the value they just wrote rather than the stale one.
    """
    value = os.environ.get(name)
    if value is not None:
        return value

    legacy = _ALIASES.get(name)
    if legacy is None:
        return default

    legacy_value = os.environ.get(legacy)
    if legacy_value is None:
        return default

    if legacy not in _warned:
        _warned.add(legacy)
        log.warning(
            "env.deprecated_name",
            deprecated=legacy,
            use=name,
            detail=f"{legacy} still works but will be removed; rename it to {name}.",
        )
    return legacy_value


def resolve_home() -> Path:
    """Where state lives, adopting a pre-rename directory rather than replacing it.

    Order:
      1. an explicit `PRAXIS_HOME` / `ORCHESTRATOR_HOME`
      2. `~/.praxis` if it exists
      3. `~/.orchestrator` if it exists  -> **adopted in place**
      4. otherwise `~/.praxis` (a fresh install)

    Step 3 is deliberately adoption, not migration. A MOVE breaks rolling back
    to the previous version, and a COPY duplicates the encrypted secret store —
    the one file that must never exist twice, because two copies wrapped by the
    same master key is how you get an orphaned half. Adoption keeps exactly one
    database and is reversible.

    The alternative — defaulting to `~/.praxis` and creating it empty — is the
    failure this function exists to prevent: the operator's plans, cycles and
    encrypted provider keys are all in the old directory, and an empty new one
    looks exactly like a working fresh install.
    """
    explicit = env("PRAXIS_HOME")
    if explicit:
        return Path(explicit)

    current = Path.home() / CURRENT_HOME_DIRNAME
    if current.exists():
        return current

    legacy = Path.home() / LEGACY_HOME_DIRNAME
    if legacy.exists():
        if "home" not in _warned:
            _warned.add("home")
            log.warning(
                "state.legacy_home_adopted",
                path=str(legacy),
                detail=(
                    f"Using the pre-rename state directory {legacy}. It is used in "
                    f"place — nothing was moved or copied. To adopt the new location, "
                    f"move it to {current} while no worker is running."
                ),
            )
        return legacy

    return current
