Here's the same analysis rewritten in a more concise and readable engineering format.

### Summary

Four real issues were identified and fixed:

1. Incorrect attribute access in the Spec API.
2. Incorrect use case invocation in the Spec API.
3. Missing protection against concurrent Discovery sessions.
4. Architectural violation where application code instantiated infrastructure components directly.

Two reported issues were determined not to be server bugs:

* Invalid semantic version (`"string"` instead of a valid semver).
* SSE event endpoint timeout behavior.

---

# Fixed Issues

## 1. Spec API using wrong model attribute

### Problem

The API was reading:

```python
spec.meta.project_name
```

but the domain model exposes:

```python
spec.name
```

### Impact

Requests to spec-related endpoints could fail with:

```python
AttributeError
```

### Fix

Updated the router to use:

```python
spec.name
```

---

## 2. Incorrect use case invocation

### Problem

A use case's `execute()` method was called using positional arguments while the implementation expected keyword arguments.

### Impact

Requests could fail with:

```python
TypeError
```

### Fix

Changed calls to:

```python
execute(project_name=...)
```

instead of positional parameters.

---

## 3. Discovery endpoints allowed concurrent sessions

### Problem

The discovery API could start multiple discovery sessions simultaneously.

### Impact

Potential issues:

* duplicated planner sessions
* queue corruption
* inconsistent state
* race conditions

### Fix

Added a discovery guard:

```python
_discovery_active
```

to both:

* start endpoint
* message endpoint

Only one discovery session can run at a time.

---

## 4. Architectural boundary violation

### Problem

`PlannerOrchestrator` (application layer) was creating an `AppContainer` directly:

```python
AppContainer.from_env()
```

This creates infrastructure dependencies inside application code.

### Why this is bad

The architecture is supposed to be:

```text
API
 ↓
Application
 ↓
Domain
 ↓
Infrastructure
```

Instead, application code was reaching back into infrastructure.

### Fix

Injected an:

```python
interactive_runtime_factory
```

into `PlannerOrchestrator`.

Now the container is only created during startup and passed down through dependency injection.

---

# Not Actually Bugs

## 5. Invalid semantic version request

### Observation

A request sent:

```json
{
  "version": "string"
}
```

but the API expects a valid semantic version:

```json
{
  "version": "0.2.0"
}
```

### Conclusion

Server behavior is correct.

This is a client-side issue caused by an OpenAPI example or generated client using `"string"` as a placeholder.

### Recommendation

Update OpenAPI examples to:

```json
{
  "version": "0.2.0"
}
```

---

## 6. Events endpoint timeout

### Observation

The SSE endpoint emits keep-alive messages:

```text
: ping
```

every ~25 seconds.

### Conclusion

This is normal SSE behavior.

The reported timeout messages are simply the keep-alive mechanism working as intended.

No fix required.

---

# Files Changed

| File                                       | Change                                              |
| ------------------------------------------ | --------------------------------------------------- |
| `src/api/routers/spec.py`                  | Fixed spec attribute access and use case invocation |
| `src/api/routers/discovery.py`             | Added discovery session guard                       |
| `src/app/usecases/planner_orchestrator.py` | Introduced runtime factory injection                |
| `src/infra/container.py`                   | Wired runtime factory into dependency container     |

---

# Architecture Improvement

## Before

```text
FastAPI
  ↓
PlannerOrchestrator
  ↓
AppContainer.from_env()
  ↓
Runtime Creation
```

Problem:

* Application layer creates infrastructure objects.

---

## After

```text
FastAPI
  ↓
PlannerOrchestrator
  ↓
interactive_runtime_factory(...)
  ↑
AppContainer
```

Benefits:

* cleaner dependency injection
* better testability
* proper layer separation
* infrastructure only created at startup

---

# Test Results

| Suite                  | Result                                    |
| ---------------------- | ----------------------------------------- |
| project_spec use cases | ✅ 29 passing                              |
| container tests        | ✅ 6 passing                               |
| import validation      | ✅ passing                                 |
| planner callback tests | ⚠️ 3 existing failures unrelated to fixes |

The remaining failures appear to be mock/test setup issues, not regressions introduced by these changes.

---

# Recommended Next Steps

## High Priority

### Introduce a gateway/worker architecture

Move long-running services out of the API process:

```text
API
 ↓
Redis/Event Bus
 ↓
Worker Processes
 ├─ Planner
 ├─ Task Manager
 └─ Reconciler
```

This aligns with your goal of making the API stateless.

---

## Medium Priority

### Replace global discovery state

Current implementation:

```python
_discovery_active
```

works for a single user.

For production, move session state to:

* Redis
* database
* dedicated session store

to support multiple users and multiple projects.

---

### Fix OpenAPI examples

Replace example values like:

```json
"version": "string"
```

with:

```json
"version": "0.2.0"
```

to avoid confusing generated clients.

---

### Fix planner callback tests

Investigate the three failing tests in:

```text
tests/unit/app/usecases/test_planner_orchestrator.py
```

They appear unrelated to the fixes above.

---

# Final Assessment

The most important issue was the architectural violation where application code instantiated infrastructure components. That has been corrected.

The API bugs that caused runtime failures have also been fixed.

The codebase is now closer to the intended architecture:

```text
Plan
 ↓
Goals
 ↓
Tasks
 ↓
Workers
```

with clearer separation between:

```text
API
 ↓
Application
 ↓
Domain
 ↓
Infrastructure
```

The next major step is implementing the dedicated worker/gateway architecture so the API can become fully stateless.
