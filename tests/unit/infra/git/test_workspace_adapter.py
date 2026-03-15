import pytest
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch
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
    @patch("subprocess.run")
    def test_create_workspace_failure(self, mock_run, tmp_path):
        mock_run.side_effect = subprocess.CalledProcessError(1, "clone", stderr="clone failed")
        adapter = GitWorkspaceAdapter(workspace_base=tmp_path)
        with pytest.raises(subprocess.CalledProcessError) as exc:
            adapter.create_workspace("git://repo", "t1")
        assert "clone failed" in str(exc.value.stderr)

    @patch("subprocess.run")
    def test_checkout_failure(self, mock_run, tmp_path):
        # First call (clone) succeeds, second (checkout) fails
        mock_run.side_effect = [
            MagicMock(returncode=0), # clone
            subprocess.CalledProcessError(1, "checkout", stderr="checkout failed") # checkout
        ]
        adapter = GitWorkspaceAdapter(workspace_base=tmp_path)
        with pytest.raises(subprocess.CalledProcessError) as exc:
            adapter.checkout_main_and_create_branch("/tmp/ws", "b1")
        assert "checkout failed" in str(exc.value.stderr)

    @patch("subprocess.run")
    def test_apply_changes_failure(self, mock_run, tmp_path):
        mock_run.side_effect = subprocess.CalledProcessError(1, "commit", stderr="commit failed")
        adapter = GitWorkspaceAdapter(workspace_base=tmp_path)
        with pytest.raises(subprocess.CalledProcessError) as exc:
            adapter.apply_changes_and_commit("/tmp/ws", "msg")
        assert "commit failed" in str(exc.value.stderr)
