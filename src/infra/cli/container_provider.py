"""
src/infra/cli/container_provider.py — lazy AppContainer for CLI commands.

The root CLI group puts one provider in ``ctx.obj``; commands fetch the
container with ``@click.pass_obj`` + ``obj.get()`` instead of each building
their own (settings load + directory creation per build).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.infra.container import AppContainer


class LazyContainerProvider:
    """Build the AppContainer at most once per CLI invocation, on first use.

    Lazy so commands that must run without valid config (init, the wizard)
    don't trip an eager settings load in the group callback. ``mode`` lets
    a --dry-run flag override the resolved mode explicitly.
    """

    def __init__(self) -> None:
        self._container: "AppContainer | None" = None
        self._mode: str | None = None

    def get(self, mode: str | None = None) -> "AppContainer":
        from src.infra.container import AppContainer

        if self._container is None or (mode is not None and mode != self._mode):
            self._container = AppContainer.from_env(mode=mode)
            self._mode = mode
        return self._container
