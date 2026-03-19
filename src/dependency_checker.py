"""Backward-compat re-export. Module moved to src/infra/dependency_checker.py"""
from src.infra.dependency_checker import (  # noqa: F401
    DependencyChecker,
    DependencyReport,
    DepResult,
    RUNTIME_DEFINITIONS,
    _check_binary,
    _check_git,
    _check_redis,
)
