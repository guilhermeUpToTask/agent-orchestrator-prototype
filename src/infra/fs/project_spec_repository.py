"""
src/infra/fs/project_spec_repository.py — YAML filesystem adapter for
ProjectSpecRepository.

Persistence layout:
  ~/.orchestrator/projects/<project_name>/project_spec.yaml

Atomicity:
  Uses AtomicFileWriter (temp-file + fsync + rename) so a crash mid-write
  never corrupts the previous version.

Deserialization:
  Raw YAML → dict → ProjectSpec.from_dict() → domain aggregate.
  Any schema violation is surfaced as SpecValidationError (domain error),
  never as a raw pydantic or YAML exception.

Serialization:
  ProjectSpec.to_dict() → yaml.dump() → AtomicFileWriter.write_text().
  The YAML always contains human-readable block style with sorted keys
  so git diffs are clean.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.domain.project_spec.aggregate import ProjectSpec
from src.domain.project_spec.errors import SpecNotFoundError, SpecValidationError
from src.domain.project_spec.repository import ProjectSpecRepository
from src.infra.fs.atomic_writer import AtomicFileWriter


_SPEC_FILENAME = "project_spec.yaml"


class FileProjectSpecRepository(ProjectSpecRepository):
    """
    Reads and writes ProjectSpec aggregates as YAML files.

    Directory layout:
      <orchestrator_home>/projects/<project_name>/project_spec.yaml

    Args:
      orchestrator_home: Root orchestrator directory.  Defaults to
        ``~/.orchestrator`` when not provided, consistent with OrchestratorConfig.
    """

    def __init__(self, orchestrator_home: str | Path) -> None:
        self._home = Path(orchestrator_home)

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _project_dir(self, project_name: str) -> Path:
        return self._home / "projects" / project_name

    def _spec_path(self, project_name: str) -> Path:
        return self._project_dir(project_name) / _SPEC_FILENAME

    # ------------------------------------------------------------------
    # Port implementation
    # ------------------------------------------------------------------

    def load(self, project_name: str) -> ProjectSpec:
        """
        Deserialise project_spec.yaml for *project_name*.

        Raises:
          SpecNotFoundError   — file missing.
          SpecValidationError — file present but malformed or schema-invalid.
        """
        path = self._spec_path(project_name)

        if not path.exists():
            raise SpecNotFoundError(project_name)

        raw_text = path.read_text(encoding="utf-8")
        data = self._parse_yaml(project_name, raw_text)
        return self._deserialize(project_name, data)

    def save(self, spec: ProjectSpec) -> None:
        """
        Atomically write *spec* to disk.

        The project directory is created if it does not yet exist.
        The write uses AtomicFileWriter so a crash mid-write is safe.
        """
        path = self._spec_path(spec.name)
        path.parent.mkdir(parents=True, exist_ok=True)

        content = self._serialize(spec)
        AtomicFileWriter.write_text(path, content)

    def exists(self, project_name: str) -> bool:
        """Cheap existence check — does not deserialise the file."""
        return self._spec_path(project_name).exists()

    # ------------------------------------------------------------------
    # Serialization / deserialization
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_yaml(project_name: str, raw_text: str) -> dict[str, Any]:
        """Parse raw YAML text into a plain dict, raising SpecValidationError on failure."""
        try:
            data = yaml.safe_load(raw_text)
        except yaml.YAMLError as exc:
            raise SpecValidationError(
                project_name, f"YAML parse error: {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise SpecValidationError(
                project_name,
                "project_spec.yaml must be a YAML mapping at its root.",
            )
        return data

    @staticmethod
    def _deserialize(project_name: str, data: dict[str, Any]) -> ProjectSpec:
        """Convert a raw dict to a ProjectSpec aggregate, wrapping errors cleanly."""
        try:
            return ProjectSpec.from_dict(data)
        except (ValueError, KeyError, TypeError) as exc:
            raise SpecValidationError(project_name, str(exc)) from exc

    @staticmethod
    def _serialize(spec: ProjectSpec) -> str:
        """Render a ProjectSpec to canonical YAML text."""
        data = spec.to_dict()
        return yaml.dump(
            data,
            default_flow_style=False,
            sort_keys=True,
            allow_unicode=True,
            indent=2,
        )
