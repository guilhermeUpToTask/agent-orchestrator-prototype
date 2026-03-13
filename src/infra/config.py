import os
from dataclasses import dataclass


@dataclass
class OrchestratorConfig:
    """
    Centralized configuration for the agent orchestrator.
    Currently loaded entirely from environment variables.
    In the future, this will also merge values from spec.toml.
    """
    mode: str
    agent_id: str
    redis_url: str
    task_timeout: int
    
    home_dir: str
    tasks_dir: str
    registry_path: str
    repo_url: str
    workspace_dir: str

    @classmethod
    def from_env(cls) -> "OrchestratorConfig":
        mode = os.getenv("AGENT_MODE", "dry-run")
        agent_id = os.getenv("AGENT_ID", "agent-worker-001")
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        task_timeout = int(os.getenv("TASK_TIMEOUT_SECONDS", "600"))

        home = os.path.abspath(os.getenv("ORCHESTRATOR_HOME", os.path.expanduser("~/.orchestrator")))
        tasks_dir = os.getenv("TASKS_DIR", os.path.join(home, "tasks"))
        registry_path = os.getenv("REGISTRY_PATH", os.path.join(home, "agents", "registry.json"))
        repo_url = os.getenv("REPO_URL", f"file://{os.path.join(home, 'repos', 'my-repo')}")
        workspace_dir = os.getenv("WORKSPACE_DIR", os.path.join(home, "repos", "workspaces"))

        return cls(
            mode=mode,
            agent_id=agent_id,
            redis_url=redis_url,
            task_timeout=task_timeout,
            home_dir=home,
            tasks_dir=tasks_dir,
            registry_path=registry_path,
            repo_url=repo_url,
            workspace_dir=workspace_dir,
        )

# Global configuration instance
config = OrchestratorConfig.from_env()
