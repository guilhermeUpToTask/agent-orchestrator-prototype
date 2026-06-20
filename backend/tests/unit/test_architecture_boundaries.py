"""
Automated guard for the hexagonal boundaries (codifies the manual greps from the
control-plane review). Fails loudly if a future change reintroduces drift.
"""
from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"


def _py_files(*parts: str):
    root = SRC.joinpath(*parts)
    return list(root.rglob("*.py")) if root.exists() else []


def _imports_any(path: Path, patterns: list[str]) -> list[str]:
    text = path.read_text(encoding="utf-8")
    hits = []
    for line in text.splitlines():
        s = line.strip()
        if not (s.startswith("import ") or s.startswith("from ")):
            continue
        for pat in patterns:
            if pat in s:
                hits.append(f"{path}: {s}")
    return hits


def test_domain_does_not_import_outer_layers() -> None:
    offenders: list[str] = []
    for f in _py_files("domain"):
        offenders += _imports_any(f, ["src.app", "src.infra", "src.api"])
    assert not offenders, "domain must not import app/infra/api:\n" + "\n".join(offenders)


def test_app_does_not_import_infra_or_api() -> None:
    offenders: list[str] = []
    for f in _py_files("app"):
        offenders += _imports_any(f, ["src.infra", "src.api"])
    assert not offenders, "app must not import infra/api:\n" + "\n".join(offenders)


def test_no_sqlalchemy_in_domain_or_api() -> None:
    offenders: list[str] = []
    for f in _py_files("domain") + _py_files("api"):
        offenders += _imports_any(f, ["sqlalchemy"])
    assert not offenders, "no SQLAlchemy in domain/api:\n" + "\n".join(offenders)


def test_no_api_dtos_in_domain_or_infra_db() -> None:
    offenders: list[str] = []
    for f in _py_files("domain") + _py_files("infra", "db"):
        offenders += _imports_any(f, ["src.api"])
    assert not offenders, "no API schemas in domain/infra.db:\n" + "\n".join(offenders)


def test_get_secret_value_confined_to_secret_store() -> None:
    # Require a real receiver (identifier or `)`) before the dot so docstring
    # mentions like ``.get_secret_value()`` (backtick-prefixed) don't match.
    call = re.compile(r"[\w)]\.get_secret_value\(")
    offenders: list[str] = []
    for f in SRC.rglob("*.py"):
        for line in f.read_text(encoding="utf-8").splitlines():
            if call.search(line):
                offenders.append(f"{f}: {line.strip()}")
    # Only the secret-store adapter may unwrap plaintext.
    assert offenders, "expected exactly one .get_secret_value() call site, found none"
    assert all("infra/db/secret_store.py" in o.replace("\\", "/") for o in offenders), (
        ".get_secret_value() must live only in secret_store.py:\n" + "\n".join(offenders)
    )
