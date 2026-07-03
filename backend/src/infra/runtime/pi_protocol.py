"""
src/infra/runtime/pi_protocol.py — the pi stdio contract, isolated (roadmap 2.4).

SEAM / NOT YET IMPLEMENTED. The full pi integration streams NDJSON runtime
events (tool calls, steps, tokens) over stdio into the AgentEventSink, tagged
by attempt. The contract must be verified against the REAL pi build; isolating
it in this one module makes a pi contract change a one-file fix.

Until then PiAgentRunner runs pi in one-shot `-p` mode (cli_runner.py) and the
sink receives start/finish events only.
"""
from __future__ import annotations
