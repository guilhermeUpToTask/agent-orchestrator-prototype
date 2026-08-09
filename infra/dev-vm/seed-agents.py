#!/usr/bin/env python3
"""Recreate the free-tier OpenRouter agent roster against a running orchestrator.

Why this exists: `seed demo` seeds capabilities, ONE default agent, a provider
and the config keys. The six-agent roster this project runs on was assembled by
hand on top of that, so a rebuilt guest loses it. The guest is cattle, so the
roster lives here as code.

Two things this must NOT assume, both learned the hard way:

* **Resource ids are server-generated UUIDs.** Only `seed demo` writes
  deterministic ids like `openrouter:<model-name>`. Anything created through
  the API gets a UUID, so a model must be referenced by looking its id up by
  NAME, never by constructing `provider:name`.
* **`POST /api/agents` does not deduplicate by name.** Re-running blindly gives
  you two `dev-agent`s with different ids. Existing names are skipped here.

Stdlib only — no requests, no jq.

    export OPENROUTER_API_KEY=...       # first run only; stored encrypted
    export ORCHESTRATOR_API_TOKEN=...   # only if the server has auth enabled
    ./seed-agents.py [BASE_URL]         # default http://127.0.0.1:8000
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.environ.get("ORCHESTRATOR_API_TOKEN", "").strip()


def call(method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw
    except urllib.error.URLError as e:
        print(f"ERROR: cannot reach {BASE}: {e.reason}", file=sys.stderr)
        print("Start it with: uv run python -m agent_orchestrator.infra.cli.main "
              "serve --port 8000", file=sys.stderr)
        raise SystemExit(1)


def report(label: str, code: int, payload: object, skipped: bool = False) -> bool:
    if skipped:
        print(f"  skip   {label}")
        return True
    if 200 <= code < 300:
        print(f"  ok     {label}")
        return True
    if code == 409:
        print(f"  exists {label}")
        return True
    print(f"  FAIL   {label} (HTTP {code})")
    print(f"         {json.dumps(payload)[:300]}")
    return False


CAPABILITIES = [
    ("backend", "Backend", "server-side code"),
    ("frontend", "Frontend", "UI code"),
    ("testing", "Testing", "tests and QA"),
    ("test_authoring", "Test authoring",
     "authors authoritative tests before implementation"),
    ("implementation", "Implementation",
     "implements changes against frozen tests"),
    ("go", "Go", "Go language code"),
    ("http", "HTTP", "HTTP server/handlers"),
    ("json", "JSON", "JSON encoding"),
]

MODELS = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "poolside/laguna-s-2.1:free",
    "google/gemma-4-31b-it:free",
    "openai/gpt-oss-20b:free",
]

# The TDD pair carries kind_attempt_ceiling.verification_error=2 (un-freeze #17:
# a rejected candidate is retryable, but capped). The plain implementers do not.
RETRY_CEIL = {
    "max_attempts": 5, "initial_backoff_seconds": 30.0, "backoff_multiplier": 2.0,
    "max_backoff_seconds": 900.0, "jitter_ratio": 0.2,
    "kind_max_attempts": {"rate_limit": 6, "connection_error": 5},
    "kind_backoff_scale": {"rate_limit": 4.0},
    "kind_attempt_ceiling": {"verification_error": 2},
    "non_retryable_kinds": ["auth_error", "verification_error", "token_limit"],
}
RETRY_PLAIN = {k: v for k, v in RETRY_CEIL.items() if k != "kind_attempt_ceiling"}

IMPL_INSTR = "Implement the task exactly as described."
TEST_INSTR = (
    "You are a TEST AUTHOR working test-first (TDD). Do NOT implement the "
    "feature. Author executable, runnable tests that precisely specify the "
    "task's acceptance criteria and will FAIL against the current code. Create "
    "real test files in the repo and provide the exact executable command(s) "
    "that run them. Your output MUST include at least one executable check; "
    "producing no executable checks is a failure."
)
IMPL_CAPS = ["backend", "frontend", "go", "http", "implementation", "json", "testing"]
TEST_CAPS = IMPL_CAPS + ["test_authoring"]

# name, role, model_role, instructions, capabilities, retry, MODEL NAME
AGENTS = [
    ("dev-agent", "implementer", "smart", IMPL_INSTR, IMPL_CAPS, RETRY_CEIL,
     "nvidia/nemotron-3-ultra-550b-a55b:free"),
    ("test-agent", "test_author", "smart", TEST_INSTR, TEST_CAPS, RETRY_CEIL,
     "nvidia/nemotron-3-ultra-550b-a55b:free"),
    ("impl-laguna-s-2.1", "implementer", "smart", IMPL_INSTR, IMPL_CAPS, RETRY_PLAIN,
     "poolside/laguna-s-2.1:free"),
    ("impl-gemma-4-31b-it", "implementer", "cheap", IMPL_INSTR, IMPL_CAPS, RETRY_PLAIN,
     "google/gemma-4-31b-it:free"),
    ("impl-gpt-oss-20b", "implementer", "cheap", IMPL_INSTR, IMPL_CAPS, RETRY_PLAIN,
     "openai/gpt-oss-20b:free"),
    ("test-laguna", "test_author", "smart", TEST_INSTR, TEST_CAPS, RETRY_PLAIN,
     "poolside/laguna-s-2.1:free"),
]


def main() -> int:
    code, _ = call("GET", "/health")
    if code != 200:
        print(f"ERROR: {BASE}/health returned {code}", file=sys.stderr)
        return 1

    ok = True

    print("== capabilities ==")
    for cid, name, desc in CAPABILITIES:
        c, p = call("POST", "/api/capabilities",
                    {"id": cid, "name": name, "description": desc, "tools": []})
        ok &= report(cid, c, p)

    print("== provider ==")
    code, providers = call("GET", "/api/providers")
    have_provider = any(p.get("name") == "openrouter" for p in (providers or []))
    if have_provider:
        report("openrouter", 409, None)
    elif not os.environ.get("OPENROUTER_API_KEY"):
        print("  FAIL   openrouter — OPENROUTER_API_KEY is unset and no provider exists")
        return 1
    else:
        c, p = call("POST", "/api/providers", {
            "name": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": os.environ["OPENROUTER_API_KEY"],
            "capacity_scope": "per_model",
        })
        ok &= report("openrouter", c, p)

    code, providers = call("GET", "/api/providers")
    provider_id = next(p["id"] for p in providers if p.get("name") == "openrouter")

    print("== models (free tier, max_inflight 2) ==")
    for mname in MODELS:
        c, p = call("POST", f"/api/providers/{provider_id}/models",
                    {"name": mname, "max_inflight": 2})
        ok &= report(mname, c, p)

    # Resolve ids by NAME. Server-generated, never constructible.
    code, models = call("GET", "/api/models")
    by_name = {m["name"]: m["id"] for m in (models or [])}

    print("== agents ==")
    code, existing = call("GET", "/api/agents")
    have = {a["name"] for a in (existing or [])}
    for name, role, mrole, instr, caps, retry, model_name in AGENTS:
        if name in have:
            report(name, 0, None, skipped=True)
            continue
        mid = by_name.get(model_name)
        if not mid:
            print(f"  FAIL   {name} — model {model_name!r} not in the catalog")
            ok = False
            continue
        c, p = call("POST", "/api/agents", {
            "name": name, "role": role, "model_role": mrole,
            "instructions": instr, "capability_ids": caps,
            "default_retry": retry, "runtime_type": "pi",
            "provider_id": provider_id, "model_id": mid,
        })
        ok &= report(name, c, p)

    print("== default agent ==")
    code, agents = call("GET", "/api/agents")
    dev = next((a for a in agents if a["name"] == "dev-agent"), None)
    if dev:
        c, p = call("POST", f"/api/agents/{dev['id']}/default")
        ok &= report(f"dev-agent ({dev['id']}) is default", c, p)
    else:
        print("  FAIL   no dev-agent to make default")
        ok = False

    dupes = {}
    for a in agents:
        dupes.setdefault(a["name"], []).append(a["id"])
    stray = {n: ids for n, ids in dupes.items() if len(ids) > 1}
    if stray:
        print("\nWARNING: duplicate agent names — the API does not dedupe by name.")
        for n, ids in stray.items():
            print(f"  {n}: {', '.join(ids)}")
        print("  Remove the extras with: DELETE /api/agents/<id>")

    print(f"\n{len(agents)} agents, {len(by_name)} models, "
          f"{len(CAPABILITIES)} capabilities expected.")
    print("Config keys live in the orchestrator scope; verify with `config list`:")
    print("  reasoner.mode=llm  reasoner.provider_id=<openrouter id>")
    print("  reasoner.model_id=<nemotron id>  agent_runner.mode=real")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
