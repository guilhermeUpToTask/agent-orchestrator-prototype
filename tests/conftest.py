# tests/conftest.py
import sys
from pathlib import Path

# Ensure src is importable from project root
sys.path.insert(0, str(Path(__file__).parent.parent))
