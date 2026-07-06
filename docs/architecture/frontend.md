# The frontend — React dashboard

*A thin, live view over the API: React Query owns server state, one SSE bridge keeps it fresh, zustand holds only UI/ephemeral state.*

Code anchors: `frontend/src/App.tsx` (routes + shell), `lib/api.ts` (fetch layer), `lib/queries.ts` (React Query hooks + the SSE bridge), `store/plannerStore.ts` (zustand), `types/generated/` (OpenAPI-generated) + `types/ui.ts` (the hand-declared plan detail model).

Stack: React 18 · Vite · TypeScript strict (no `any`) · @tanstack/react-query v5 · zustand · react-router v6 · @xyflow/react + dagre (the goals canvas) · CSS modules with a theme system (IBM Plex).

## Screens and shell

```mermaid
flowchart TB
    subgraph routes["Routes"]
        plans["/ — PlansView<br/>plan list + 'New plan' composer"]
        shell["/plans/:id/* — PlanShell"]
        settings["/settings — SettingsLayout"]
    end

    subgraph shellparts["PlanShell layout"]
        rail["LifecycleRail<br/>9-phase stepper"]
        view["Overview | Goals | Agents | Activity<br/>(+ StaleNotice when the stream drops)"]
        dock["ConsoleDock<br/>live agent.event log"]
        chat["ChatPanel<br/>discovery / replanning turns"]
        gate["GatePanel<br/>the two gate dialogs"]
    end

    subgraph settingsparts["Settings sections (full CRUD)"]
        s1["Capabilities · Agents (runtime bindings) ·<br/>Providers/Models · Projects ·<br/>Reasoner · Runner (status + mode)"]
    end

    shell --> shellparts
    settings --> settingsparts
```

- **GoalsView** renders the goal/task tree as a two-level dagre-laid-out flow graph (`lib/layout.ts`): goal group nodes containing task nodes with status badges.
- **GatePanel** is where the human gates live: AWAITING_REVIEW → approve (with surgical-edit affordances via `POST /edits`), REVIEW → finish or replan.
- **ChatPanel** is enabled only in the conversational phases; a send POSTs the message and appends the reply from the HTTP body (`MessageResponse{reply, committed, phase}`) — it does not wait on SSE. History hydrates from `GET /plans/{id}/chat`.

## Data flow — one source of truth per kind of state

```mermaid
flowchart LR
    api["lib/api.ts<br/>typed fetch over<br/>types/generated/"] --> rq["React Query caches<br/>['plans'] · ['plan', id] · ['chat', id] ·<br/>reference + config + status keys"]
    sse["useSSEBridge<br/>EventSource /api/events<br/>named listeners, event_id dedup"] -- "invalidate affected keys" --> rq
    sse -- "agent.event lines,<br/>connection state,<br/>event buffer" --> zs["zustand plannerStore<br/>UI/ephemeral only"]
    rq --> comp["Components"]
    zs --> comp
    comp -- mutations --> api
```

The division of labor that keeps this simple:

- **Server state lives in React Query.** SSE events don't carry state — they *invalidate* the affected query keys (`PhaseAdvanced`, `Task*`, `Goal*`, `Plan*` events → refetch that plan; reference/config mutations → refetch catalogs + status). Payloads stay minimal by backend contract, so the UI always re-reads truth instead of patching caches.
- **zustand holds what the server doesn't own**: the SSE connection state, a bounded buffered-event feed (Activity), agent console lines (ConsoleDock), and toasts.
- **Degradation is explicit**: when the stream drops, the main view dims behind a `StaleNotice` ("showing data as of …") and reconnection triggers a blanket `invalidateQueries()` — the refetch-on-reconnect strategy that makes SSE replay unnecessary.

## Type generation

`npm run generate:api` exports the backend's OpenAPI schema (`backend/scripts/export_openapi.py`) and runs `openapi-ts` into `src/types/generated/`. Operation IDs are stable (`plans-create`) via the backend's `generate_unique_id_function`. One exception is hand-maintained: the **plan detail read model** (the full aggregate document returned by `GET /plans/{id}`) is declared in `src/types/ui.ts` — keep it in sync with the domain when the aggregate changes.

## Conventions

- Strictly typed; no `any`. DTOs come from `types/generated/` — never hand-redefine them.
- Mutations follow one shape (`lib/queries.ts`): invalidate the affected keys on success, toast the error envelope's `message` on failure.
- Reusable primitives live in `components/ui/` (Button, Card, Dialog, Field, Input, Select, ConfirmAction — destructive actions are two-step).
- Dev: `npm run dev` (Vite, port 5173 — already in the API's default CORS list). Build: `npm run build` (tsc + vite).
