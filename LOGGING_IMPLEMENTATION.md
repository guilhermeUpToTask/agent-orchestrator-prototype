# Live Logging & Observability Implementation

## Summary

This document describes the implementation of the live logging/observability feature for the agent orchestrator CLI.

## Requirements Met

✅ **Real-time streaming**: Logs are streamed to terminal as they arrive using non-blocking I/O with `select()`
✅ **Per-agent scoping**: Consistent prefix format `[timestamp] [agent-name] [event]: message`
✅ **Event capture**: All specified events are captured and emitted
✅ **Concurrency safety**: Per-agent locks prevent interleaved output
✅ **Non-intrusive**: Wrappers/adapters around existing runtime implementations
✅ **Structured JSON**: Internal representation uses JSON while rendering readable terminal output

## Files Added

### Core Logging Module

1. **`src/infra/logging/__init__.py`**
   - Package initialization with exports

2. **`src/infra/logging/log_events.py`**
   - Defines `LogEvent` dataclass with structured JSON representation
   - Defines `LogEventType` enum with all event types
   - Event builder functions for each event type

3. **`src/infra/logging/live_logger.py`**
   - `LiveLogger` class: Thread-safe logger with per-agent state
   - Real-time terminal output with color coding
   - JSON file storage for programmatic access
   - Global logger singleton pattern

4. **`src/infra/logging/runtime_wrapper.py`**
   - `LoggingRuntimeWrapper`: Wraps any `AgentRuntimePort`
   - Intercepts all runtime method calls
   - Streams stdout/stderr in real-time using `subprocess.Popen` + `select()`
   - Emits lifecycle events (START, END, LLM, TOOL, etc.)
   - Falls back gracefully if interception fails

### Documentation

5. **`src/infra/logging/README.md`**
   - Comprehensive documentation of the logging system
   - Usage examples and configuration details

6. **`src/infra/logging/demo.py`**
   - Demo script showing concurrent agent logging
   - Demonstrates all event types and thread safety

### Tests

7. **`tests/unit/infra/logging/test_live_logger.py`**
   - Unit tests for LiveLogger
   - Tests for LogEvent and event builders
   - Concurrent logging tests

### Updated Files

8. **`src/infra/runtime/factory.py`**
   - Updated to automatically wrap all runtimes with `LoggingRuntimeWrapper`

9. **`tests/unit/infra/runtime/test_pi_runtime.py`**
   - Updated to account for `LoggingRuntimeWrapper` wrapper

10. **`tests/unit/infra/test_factory.py`**
    - Updated to account for `LoggingRuntimeWrapper` wrapper

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Application Layer                        │
│  (TaskExecuteUseCase, WorkerHandler, CLI commands)          │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                 Runtime Factory                             │
│  build_agent_runtime() → wraps with LoggingRuntimeWrapper   │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│             LoggingRuntimeWrapper                           │
│  • Intercepts start_session()                               │
│  • Intercepts wait_for_completion()                         │
│  • Intercepts terminate_session()                           │
│  • Emits AGENT_START, AGENT_END events                      │
│  • Streams stdout/stderr in real-time                       │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                  Base Runtime                               │
│  (ClaudeCodeRuntime, GeminiAgentRuntime, PiAgentRuntime)    │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                 LiveLogger                                  │
│  • Per-agent thread-safe locking                            │
│  • Real-time terminal output                                │
│  • JSON file storage                                        │
└─────────────────────────────────────────────────────────────┘
```

## Event Types Captured

| Event Type | Terminal Label | Description |
|------------|----------------|-------------|
| `AGENT_START` | START | Agent session started |
| `AGENT_END` | END | Agent session ended |
| `LLM_REQUEST` | LLM_REQ | LLM API request |
| `LLM_RESPONSE` | LLM_RSP | LLM API response |
| `TOOL_CALL_START` | TOOL_START | Tool/function call started |
| `TOOL_CALL_END` | TOOL_END | Tool/function call completed |
| `STDOUT` | STDOUT | Standard output line |
| `STDERR` | STDERR | Standard error line |
| `AGENT_ERROR` | ERROR | Agent error occurred |
| `AGENT_OUTPUT` | OUTPUT | Captured agent output |

## Terminal Output Format

```
[0.12s] [pi] [START] Agent pi started
[0.45s] [pi] [STDOUT] Running tests...
[1.23s] [pi] [STDERR] Warning: deprecated function
[2.56s] [pi] [END] Agent pi ended with exit code 0
```

Colors:
- Green: START, END
- Blue: LLM_REQ, LLM_RSP
- Cyan: TOOL_START, TOOL_END
- White: STDOUT
- Red: STDERR, ERROR
- Yellow: OUTPUT

## JSON Output Format

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

## Concurrency Safety

The system handles concurrent agents safely:
- Each agent has its own lock in `LiveLogger`
- Log events are buffered per-agent
- Terminal output is synchronized at the stream level
- No interleaving between agents

## Non-intrusive Design

The wrapper pattern ensures zero changes to existing runtime code:
- Original runtime classes remain unchanged
- Wrapper intercepts method calls transparently
- Falls back to base runtime if interception fails
- Works with all runtime types

## Usage

### Automatic Integration

All runtimes are automatically wrapped by the factory:

```python
from src.infra.runtime.factory import build_agent_runtime

runtime = build_agent_runtime(agent_props)  # Already wrapped!
```

### Accessing Logs

```python
from src.infra.logging import get_logger

logger = get_logger()
events = logger.get_json_logs("pi")
```

## Testing

All existing tests pass (513 tests):
- Unit tests for new logging module
- Updated tests for runtime factory
- Integration with existing test suite

## Files Structure

```
src/infra/logging/
├── __init__.py              # Package exports
├── log_events.py           # Event definitions
├── live_logger.py          # Thread-safe logger
├── runtime_wrapper.py      # Runtime wrapper
├── README.md               # Documentation
└── demo.py                 # Demo script

tests/unit/infra/logging/
└── test_live_logger.py     # Unit tests
```

## Configuration

JSON logs are stored in the configured logs directory:
- Default: `~/.orchestrator/projects/<project>/logs/`
- Configurable via `LOGS_DIR` env var or `logs_dir` in config.json

## Future Enhancements

Potential improvements for future versions:
1. Structured logging with log levels (INFO, WARN, ERROR, DEBUG)
2. Log aggregation and search capabilities
3. Integration with external monitoring tools
4. Performance metrics tracking
5. Agent capability profiling
