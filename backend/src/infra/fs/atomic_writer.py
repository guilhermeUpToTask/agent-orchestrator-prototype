import os
import uuid
from pathlib import Path


class AtomicFileWriter:
    """
    Utility for performing durable atomic file writes on POSIX systems.
    Writes to a temporary file, fsyncs data, renames over the target file,
    and fsyncs the parent directory.
    """

    @staticmethod
    def write_text(file_path: Path, content: str) -> None:
        """Atomically write text content to the given file path."""
        # Ensure parent directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        temp_file = file_path.with_suffix(file_path.suffix + f".tmp_{uuid.uuid4().hex[:8]}")
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())

            # Atomic rename on POSIX
            os.replace(temp_file, file_path)

            # fsync the directory to ensure the rename is durable
            dir_fd = os.open(str(file_path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            if temp_file.exists():
                temp_file.unlink(missing_ok=True)
            raise
