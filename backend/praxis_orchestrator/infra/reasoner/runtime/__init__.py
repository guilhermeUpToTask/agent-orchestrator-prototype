"""The LLM planning runtime — the old planner runtime's architecture, ported.

An OpenAI-compatible async client (llm_client), a provider-agnostic
tool-calling agent loop with terminal submit tools and {accepted:false}
self-correction (agent_loop), typed tool specs (tools), transient/permanent
error classification (errors), the plan->markdown context renderer (context)
and the phase prompts (prompts). The OpenAIReasoner composes these behind the
domain Reasoner port.
"""
