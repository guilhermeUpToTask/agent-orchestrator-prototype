/**
 * `errorDetail` shows the server's message, and never the request body.
 *
 * Phase 10A found the disclosure path this locks: the backend's default 422
 * carried the submitted `api_key` inside `detail[].input`; `detail` was an
 * ARRAY so the string branch missed it; the helper fell through to the raw
 * message; and the operator got their own key rendered in a toast. The backend
 * fix is `VALIDATION_ERROR` in the one envelope — this file locks the second
 * half, so an unrecognised body can never be printed verbatim again.
 */
import { describe, expect, it } from "vitest";

import { errorDetail } from "./toast";

const SECRET = "sk-live-NEVER-ECHO-THIS-abc123";

describe("errorDetail", () => {
  it("surfaces the control-plane envelope's message", () => {
    const body = JSON.stringify({
      error: { code: "PLAN_NOT_FOUND", message: "No plan 'abc'", request_id: "r1" },
    });

    expect(errorDetail(new Error(`GET /api/plans/abc → 404: ${body}`))).toBe(
      "No plan 'abc' (request r1)",
    );
  });

  it("surfaces the validation envelope the backend now returns", () => {
    const body = JSON.stringify({
      error: {
        code: "VALIDATION_ERROR",
        message: "Request validation failed — body.name: Field required",
        request_id: "r2",
      },
    });

    expect(errorDetail(new Error(`POST /api/providers → 422: ${body}`))).toContain(
      "body.name: Field required",
    );
  });

  it("surfaces a plain-string detail", () => {
    const body = JSON.stringify({ detail: "worker is not running" });

    expect(errorDetail(new Error(`GET /api/workers → 409: ${body}`))).toBe(
      "worker is not running",
    );
  });

  it("never prints an unrecognised body, even one carrying a credential", () => {
    // The exact shape FastAPI's default handler used to return.
    const body = JSON.stringify({
      detail: [
        {
          type: "missing",
          loc: ["body", "name"],
          msg: "Field required",
          input: { base_url: "https://api.example.com", api_key: SECRET },
        },
      ],
    });

    const shown = errorDetail(new Error(`POST /api/providers → 422: ${body}`));

    expect(shown).not.toContain(SECRET);
    expect(shown).not.toContain("api_key");
    expect(shown).toContain("POST /api/providers");
  });

  it("passes through an error with no body at all", () => {
    expect(errorDetail(new Error("Failed to fetch"))).toBe("Failed to fetch");
  });

  it("handles a non-Error rejection", () => {
    expect(errorDetail("boom")).toBe("boom");
  });
});
