"""Test bootstrap: make the backend root importable so `praxis_orchestrator.*`
resolves without an editable install."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
