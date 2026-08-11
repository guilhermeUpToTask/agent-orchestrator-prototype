# Phase 10A audit — sweep 3: the reasoner tool surface against hostile model output

**Date:** 2026-08-11
**Scope:** `infra/reasoner/runtime/` — the tool-calling loop, tool dispatch, and
the provider-response parsing that feeds them.
**Result:** 2 findings, both proven and both fixed. 3 areas verified clean.

Sweep 2 left this area explicitly *unproven* rather than clean: it had been read
and not exercised. This sweep exercises it, by scripting the loop with output no
well-behaved model produces.

The framing that made it worth doing: this package already declares model output
untrusted for **content** — `_validate_submission` re-validates every submitted
payload through Pydantic, with a comment naming the real model that produced a
schema-shaped-but-wrong one. Nothing was checking output that is hostile or
broken in **shape**.

Suite: 1498 → **1507 passed**, 7 skipped.

---

## Findings

### F9 — a turn's tool-call fan-out was unbounded

**Severity:** unbounded work driven by untrusted input.
**Status:** fixed.

`run_tool_session` looped over `turn.tool_calls` with no cap. A single assistant
turn carrying 500 tool calls ran **500 handlers** and appended **500 tool
messages** to the transcript — which the next request then carries upstream.

**Proof.** One scripted turn with 500 calls against a counting read handler:

```
scripted 500 tool calls in ONE turn
handler invocations : 500
messages appended   : 504
```

This is not a theoretical shape. Parallel tool calls are a normal provider
feature, the count is chosen entirely by the model, and the read handlers reach
`GitRepositoryReader` — so the work is real file reads and searches, and the
transcript growth is paid for again on every subsequent turn of the session.

**Fix.** `_MAX_TOOL_CALLS_PER_TURN = 16` — far above legitimate use (the widest
profile offers a handful of read tools) and far below the cost of an unbounded
fan-out.

**The part that took the most thought: the excess is refused, not dropped.**
The providers this speaks to require a tool message for *every* `tool_call_id`
present in the assistant message. Silently discarding the surplus would leave
the next request malformed and convert a bounded overrun into a hard failure —
so every call is still answered, and the ones past the cap get a refusal that
tells the model what happened and to ask for fewer.

### F10 — a handler's exception text was sent to the provider

**Severity:** information disclosure to a third party.
**Status:** fixed.

`execute_tool_call` ended with:

```python
except Exception as exc:
    result_str = json.dumps({"error": str(exc)})
```

That result becomes a `tool` message, and tool messages are replayed in the next
request. So the raw text of **any** unexpected exception — including absolute
paths and internal state — travelled to the model provider.

**Proof.** A handler raising `could not open
/home/dev/.orchestrator/secrets.db row 7`:

```
tool-role messages in the transcript (SENT to the provider):
   {"error": "could not open /home/dev/.orchestrator/secrets.db row 7"}
present in the 2nd provider REQUEST: True
```

**Why this is a defect and not the design.** Rejection has its own deliberate
channel: a handler refuses by *returning* `{"accepted": false, "errors": [...]}`
(`openai_reasoner._rejected`), which is what feeds self-correction. Reaching the
`except` means something unexpected broke. Forwarding that verbatim is also the
blanket-handler pattern the API layer explicitly refuses — `exceptions.py`:
*"There is deliberately NO blanket KeyError/ValueError mapping: an unmapped
builtin error is a bug"*. The tool layer was doing the opposite, silently.

**Fix.** The exception is logged locally with `exc_info` — where an operator
debugging a tool actually looks — and the model is told `The <tool> tool failed
to run. Try a different call.` It still knows the call failed, which is what it
needs in order to adapt; it no longer learns anything about the host.

An unknown tool *name* deliberately keeps its specific message: that is the
model's own mistake echoed back, and it carries no internal detail.

**One existing test had to change.**
`test_agent_loop.py::test_unknown_tool_and_handler_crash_become_error_results`
asserted `any("boom" in c for c in tool_messages)`. Its stated purpose — visible
in its name and in `assert result.submitted is True  # the loop survived both
bad calls` — is that neither bad call crashes the loop, and that still holds.
The raw-message assertion was incidental to it, and now asserts the opposite
with the reason written down.

## Verified clean

- **Malformed tool arguments cannot reach a handler.** `OpenAILLMClient.complete`
  parses `tc.function.arguments` with `json.loads` inside a `try`, falls back to
  `{}`, and then *also* rejects a non-dict result — so a provider returning
  `"[1,2,3]"` or `"not json"` as arguments produces an empty dict rather than a
  type error deep in a handler.
- **An unknown tool name does not crash the loop.** It returns
  `{"error": "Unknown tool: …"}` and the session continues; scripted and
  confirmed.
- **Two terminal calls in one turn resolve deterministically** — the first
  accepted submission wins, and the second is ignored rather than racing it.

## Not a finding

`_payload_in_prose` was read closely because it parses model prose and quotes up
to 4000 characters of it back. It is safe: it only ever *quotes* a payload for
the model to resubmit properly, never accepts one as a submission, so the
re-validation at the tool boundary cannot be bypassed through it — which its own
docstring says is the point. The empty-block edge (`block[:20]` on an empty
string) does not raise.

## Still unswept

- The frontend's error and stale-data states beyond the toast path fixed in
  sweep 1 — **now the head of the queue**.
- Remaining doc/code drift outside the files touched by these three sweeps.
