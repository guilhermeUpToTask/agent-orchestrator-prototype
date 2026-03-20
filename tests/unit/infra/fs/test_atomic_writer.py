import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from src.infra.fs.atomic_writer import AtomicFileWriter

class TestAtomicFileWriter:
    def test_write_success(self, tmp_path):
        target = tmp_path / "target.txt"
        AtomicFileWriter.write_text(target, "content")
        assert target.read_text() == "content"
        # Temporary file should be cleaned up
        assert len(list(tmp_path.glob("*.tmp"))) == 0

    def test_write_failure_cleans_up_temp(self, tmp_path):
        target = tmp_path / "target.txt"
        # Mock os.replace to fail
        with patch("os.replace", side_effect=OSError("rename failed")):
            with pytest.raises(OSError):
                AtomicFileWriter.write_text(target, "content")
        
        assert not target.exists()
        # Temporary file should be cleaned up even on failure
        assert len(list(tmp_path.glob("*.tmp"))) == 0

    def test_partial_write_preserves_original_on_rename_failure(self, tmp_path):
        target = tmp_path / "target.txt"
        target.write_text("original")
        with patch("os.replace", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                AtomicFileWriter.write_text(target, "new content")
        assert target.read_text() == "original"
        assert len(list(tmp_path.glob("*.tmp"))) == 0

    def test_atomic_write_overwrites(self, tmp_path):
        target = tmp_path / "target.txt"
        target.write_text("old")
        AtomicFileWriter.write_text(target, "new")
        assert target.read_text() == "new"
