# Live Logging & Observability

This module provides real-time logging and observability for the agent orchestrator.

## Features

- **Real-time streaming**: Logs are streamed to terminal as they arrive (no buffering)
- **Per-agent scoping**: Each agent has a consistent log prefix format: `[timestamp] [agent-name] [event]: message`
- **Thread-safe**: Multiple agents can run concurrently without log interleaving
- **Structured JSON**: Internal events are stored as JSON for programmatic access
- **Non-intrusive**: Wraps existing runtimes without modifying them

## Log Format

### Terminal Output

```
[0.12s] [pi] [START] Agent pi started
[0.45s] [pi] [STDOUT] Running tests...
[1.23s] [pi] [STDERR] Warning: deprecated function
[2.56s] [pi] [END] Agent pi ended with exit code 0
```

### JSON Events

Each event is stored as a JSON object with the following structure:

```json
{
  "event_type": "AGENT_START",
  "agent_name": "pi",
  "timestamp": 0.123,
  "message": "Agent pi started",
  "details": {
    "session_id": "pi-agent-123-1710854400",
    "workspace": "/home/user/.orchestrator/projects/default/workspaces/task-001"
  }
}
```

## Event Types

| Event Type | Description |
|------------|-------------|
| `AGENT_START` | Agent session started |
| `AGENT_END` | Agent session ended |
| `LLM_REQUEST` | LLM API request sent |
| `LLM_RESPONSE` | LLM API response received |
| `TOOL_CALL_START` | Tool/function call started |
| `TOOL_CALL_END` | Tool/function call completed |
| `STDOUT` | Standard output line |
| `STDERR` | Standard error line |
| `AGENT_ERROR` | Agent error occurred |
| `AGENT_OUTPUT` | Captured agent output |

## Usage

### Automatic Integration

The runtime factory automatically wraps all agents with logging. No code changes needed:

```python
from src.infra.runtime.factory import build_agent_runtime

# All runtimes are automatically wrapped with LoggingRuntimeWrapper
runtime = build_agent_runtime(agent_props)
```

### Manual Usage

You can also manually create a logged runtime:

```python
from src.infra.logging import LoggingRuntimeWrapper
from src.infra.runtime.factory import build_agent_runtime

# Build base runtime
base_runtime = build_agent_runtime(agent_props)

# Wrap with logging
logged_runtime = LoggingRuntimeWrapper(
    base_runtime=base_runtime,
    agent_name="my-agent",
    json_log_dir="/path/to/logs",  # Optional
)
```

### Accessing JSON Logs

```python
from src.infra.logging import get_logger

logger = get_logger()

# Get all events for an agent
events = logger.get_json_logs("pi")

# Get JSON log file path
log_path = logger.get_json_log_path("pi")
```

## Configuration

JSON logs are stored in the orchestrator's logs directory (configured via `LOGS_DIR` env var or `logs_dir` in config.json).

Default location: `~/.orchestrator/projects/<project>/logs/`

## Concurrency Safety

The logger uses per-agent locks to ensure thread-safe logging when multiple agents run concurrently. Each agent's log output is isolated and won't interleave with other agents.

## Implementation Details

### Architecture

```
AgentRuntimePort (base runtime)
    ↓
LoggingRuntimeWrapper (adds logging)
    ↓
LiveLogger (handles concurrent output)
    ↓
Terminal + JSON files
```

### Key Components

1. **LogEvent**: Structured internal representation of log events (JSON-serializable)
2. **LiveLogger**: Thread-safe logger with per-agent state management
3. **LoggingRuntimeWrapper**: Wraps any AgentRuntimePort to intercept calls and emit events
4. **Event Builders**: Convenience functions for creating specific event types

### Non-intrusive Design

The wrapper pattern allows adding logging without modifying existing runtime implementations:

- Original runtime classes remain unchanged
- Wrapper intercepts method calls and emits events
- Falls back to base runtime if interception fails
- Works with all runtime types: Gemini, Claude Code, Pi, Dry-run
