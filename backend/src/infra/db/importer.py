"""
src/infra/db/importer.py — one-way file -> SQLite config importer.

Idempotent migration of the legacy flat-file config into SQLite: provider keys
(from the environment), projects (from ``projects/<name>/project.json``), and
agent definitions (from ``agents/registry.json``). Keyed on stable ids and
skip-if-exists, so re-runs neither duplicate rows nor clobber keys an operator
has since rotated through the UI/CLI.

Never a write-back path: SQLite is authoritative after import.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import structlog
import yaml

from src.app.services.project_service import ProjectService, slugify
from src.app.services.registry_service import RegistryService
from src.domain.aggregates.task import TaskAggregate
from src.domain.errors import BaseAppException
from src.domain.repositories.config_store import ConfigStorePort
from src.domain.repositories.task_repository import TaskRepositoryPort
from src.domain.value_objects.config import ProviderKind

log = structlog.get_logger(__name__)

# runtime_type (AgentProps) -> provider kind it runs on.
_RUNTIME_TO_KIND: dict[str, ProviderKind] = {
    "claude": ProviderKind.ANTHROPIC,
    "gemini": ProviderKind.GEMINI,
    "pi": ProviderKind.OPENROUTER,
}

# provider kind -> environment variable holding its API key.
_KIND_ENV_KEY: dict[ProviderKind, str] = {
    ProviderKind.ANTHROPIC: "ANTHROPIC_API_KEY",
    ProviderKind.OPENAI: "OPENAI_API_KEY",
    ProviderKind.GEMINI: "GEMINI_API_KEY",
    ProviderKind.OPENROUTER: "OPENROUTER_API_KEY",
}


@dataclass
class ImportReport:
    providers_created: list[str] = field(default_factory=list)
    projects_created: list[str] = field(default_factory=list)
    agents_created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def import_config(
    *,
    orchestrator_home: Path,
    config_store: ConfigStorePort,
    project_service: ProjectService,
    registry_service: RegistryService,
    env: Mapping[str, str] | None = None,
) -> ImportReport:
    env = env if env is not None else os.environ
    report = ImportReport()

    _import_providers(config_store, registry_service, env, report)
    _import_projects(orchestrator_home, config_store, project_service, env, report)
    _import_agents(orchestrator_home, config_store, registry_service, report)
    return report


def _import_providers(
    config: ConfigStorePort,
    registry: RegistryService,
    env: Mapping[str, str],
    report: ImportReport,
) -> None:
    for kind, env_key in _KIND_ENV_KEY.items():
        api_key = env.get(env_key, "").strip()
        if not api_key:
            continue
        pid = kind.value
        if config.get_provider(pid) is not None:  # skip-if-exists (no clobber)
            report.skipped.append(f"provider:{pid}")
            continue
        registry.register_provider(provider_id=pid, kind=kind, api_key=api_key)
        report.providers_created.append(pid)


def _import_projects(
    orchestrator_home: Path,
    config: ConfigStorePort,
    projects: ProjectService,
    env: Mapping[str, str],
    report: ImportReport,
) -> None:
    projects_dir = orchestrator_home / "projects"
    if not projects_dir.is_dir():
        return
    github_token = env.get("GITHUB_TOKEN", "").strip() or None
    for project_dir in sorted(p for p in projects_dir.iterdir() if p.is_dir()):
        name = project_dir.name
        pid = slugify(name)
        if config.get_project(pid) is not None:
            report.skipped.append(f"project:{pid}")
            continue
        cfg = _read_json(project_dir / "project.json")
        repo_url = cfg.get("source_repo_url") or f"file://{project_dir / 'repo'}"
        projects.create_project(
            name=name,
            repo_url=repo_url,
            default_branch=cfg.get("github_base_branch", "main"),
            github_token=github_token,
            project_id=pid,
        )
        report.projects_created.append(pid)


def _import_agents(
    orchestrator_home: Path,
    config: ConfigStorePort,
    registry: RegistryService,
    report: ImportReport,
) -> None:
    projects_dir = orchestrator_home / "projects"
    if not projects_dir.is_dir():
        return
    for project_dir in sorted(p for p in projects_dir.iterdir() if p.is_dir()):
        registry_path = project_dir / "agents" / "registry.json"
        data = _read_json(registry_path)
        for agent_id, props in data.items():
            if config.get_agent(agent_id) is not None:
                report.skipped.append(f"agent:{agent_id}")
                continue
            kind = _RUNTIME_TO_KIND.get(props.get("runtime_type", ""))
            model_id = (props.get("runtime_config") or {}).get("model")
            if kind is None or not model_id:
                report.skipped.append(f"agent:{agent_id}")
                continue
            provider = config.get_provider(kind.value)
            if provider is None:  # no key imported for this provider — can't wire it
                report.skipped.append(f"agent:{agent_id}")
                continue
            try:
                registry.add_model(provider_id=kind.value, model_id=model_id)
                registry.register_agent(
                    agent_id=agent_id,
                    name=props.get("name", agent_id),
                    runtime_type=props["runtime_type"],
                    provider_id=kind.value,
                    model_id=model_id,
                    capabilities=tuple(props.get("capabilities", ())),
                )
                report.agents_created.append(agent_id)
            except BaseAppException:
                report.skipped.append(f"agent:{agent_id}")


def import_tasks(
    *,
    orchestrator_home: Path,
    task_store: TaskRepositoryPort,
    config_store: ConfigStorePort | None = None,
) -> list[str]:
    """
    Stage B migration: import per-project ``tasks/*.yaml`` files into the SQLite
    task store. Idempotent (skip-if-exists by task_id). Returns imported ids.

    A task is stamped with its owning project's id only when that project exists
    in the config store (the FK backstop); otherwise it is imported unscoped
    (``project_id=None``) so a partial migration never crashes on a missing
    parent. Run the config import first to get tasks grouped under projects.
    """
    imported: list[str] = []
    projects_dir = orchestrator_home / "projects"
    if not projects_dir.is_dir():
        return imported
    for project_dir in sorted(p for p in projects_dir.iterdir() if p.is_dir()):
        pid = slugify(project_dir.name)
        project_known = config_store is not None and config_store.get_project(pid) is not None
        tasks_dir = project_dir / "tasks"
        if not tasks_dir.is_dir():
            continue
        for path in sorted(tasks_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (yaml.YAMLError, OSError):
                log.warning("importer.unreadable_task", path=str(path))
                continue
            if not data:
                continue
            task = TaskAggregate.model_validate(data)
            if project_known and task.project_id is None:
                task.project_id = pid
            elif not project_known:
                task.project_id = None  # avoid FK violation on unknown project
            if task_store.get(task.task_id) is None:
                task_store.save(task)
                imported.append(task.task_id)
    return imported


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.warning("importer.unreadable_json", path=str(path))
        return {}
