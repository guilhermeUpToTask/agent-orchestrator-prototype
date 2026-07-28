"""A query-string token is only dangerous where it is written down.

`/api/events` accepts `?token=` because EventSource cannot send headers
(`src/api/security.py`). uvicorn's access logger would then print
`GET /api/events?token=… HTTP/1.1` on every connect and reconnect.

`RequestLoggingMiddleware` already records each request structurally with a
correlation id, status and duration — and records `request.url.path`, never the
query string — so turning uvicorn's logger off loses no observability and
removes the only place the token would land. It also retires a stdlib-logging
path the project's observability rules already forbid.
"""

from __future__ import annotations

from click.testing import CliRunner

from src.infra.cli.main import cli


def test_api_start_disables_the_uvicorn_access_log(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run(app: object, **kwargs: object) -> None:
        captured.update(kwargs)

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr("src.api.server.create_app", lambda: object())

    result = CliRunner().invoke(cli, ["api", "start", "--port", "9999"])

    assert result.exit_code == 0, result.output
    assert captured["access_log"] is False
