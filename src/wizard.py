"""Backward-compat re-export. Wizard moved to src/infra/cli/wizard/."""
from src.infra.cli.wizard import run_wizard  # noqa: F401

# Private helpers the existing tests import directly
from src.infra.cli.wizard.steps.deps     import check_and_report     as _check_and_report       # noqa: F401
from src.infra.cli.wizard.steps.config   import collect_project_config as _collect_project_config # noqa: F401
from src.infra.cli.wizard.steps.registry import setup_registry         as _setup_registry         # noqa: F401
from src.infra.cli.wizard.steps.registry import _interactive_register_agent                       # noqa: F401
