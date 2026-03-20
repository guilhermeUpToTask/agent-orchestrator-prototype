"""
src/infra/goal_file.py — Load and validate a goal file from disk.

Goal files are YAML documents that conform to the GoalSpec schema.
This module is the single entry point for reading them.

Example goal file structure:

  goal_id: goal-auth-layer        # optional; auto-generated if absent
  name: auth-layer                # used in branch names — must be slug-safe
  description: "Add JWT auth middleware and login endpoint"
  version: "1"
  tasks:
    - task_id: setup-deps
      title: "Install auth dependencies"
      description: "Add pyjwt and passlib to requirements"
      capability: coding
      files_allowed_to_modify: ["requirements.txt"]
      depends_on: []

    - task_id: add-middleware
      title: "Implement JWT middleware"
      description: "Create src/middleware/auth.py"
      capability: coding
      files_allowed_to_modify: ["src/middleware/auth.py"]
      depends_on: ["setup-deps"]
      acceptance_criteria:
        - "middleware intercepts /api/* routes"
      test_command: "pytest tests/test_auth.py -q"
"""
from __future__ import annotations

from pathlib import Path

import yaml

from src.domain.value_objects.goal import GoalSpec


def load_goal_file(path: str | Path) -> GoalSpec:
    """
    Read and validate a goal YAML file.

    Raises FileNotFoundError if the file doesn't exist.
    Raises pydantic.ValidationError if the schema is invalid.
    Raises ValueError if the dependency graph contains a cycle or
    unknown depends_on references.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Goal file not found: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    return GoalSpec.model_validate(data)
