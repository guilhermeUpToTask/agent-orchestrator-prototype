import os
import shutil
import pytest
from pathlib import Path
from src.infra.git.workspace_adapter import GitWorkspaceAdapter, _parse_git_porcelain

class TestGitWorkspaceAdapter:
    def test_parse_git_porcelain(self):
        output = "M  file1.py\n?? file2.py\nR  old.py -> new.py\n"
        files = _parse_git_porcelain(output)
        assert files == ["file1.py", "file2.py", "new.py"]

    def test_init_creates_base_dir(self, tmp_path):
        base = tmp_path / "workspaces"
        adapter = GitWorkspaceAdapter(workspace_base=base)
        assert base.exists()

    def test_cleanup_workspace(self, tmp_path):
        ws = tmp_path / "task-1"
        ws.mkdir()
        adapter = GitWorkspaceAdapter(workspace_base=tmp_path)
        adapter.cleanup_workspace(str(ws))
        assert not ws.exists()
