# Frontend — the AIPOM dashboard

React 18 + Vite + TypeScript (strict, no `any`). A thin live view over the backend API: **React Query owns server state, one SSE bridge keeps it fresh, zustand holds only UI/ephemeral state.** The full architecture write-up (with diagrams) is [`../docs/architecture/frontend.md`](../docs/architecture/frontend.md).

## Run

```bash
npm install
npm run dev            # http://localhost:5173 — expects the API on :8000
npm run build          # tsc + vite (the type gate)
npm run generate:api   # regenerate src/types/generated/ from the backend's OpenAPI
```

The dev origin is already in the backend's default CORS list. Point elsewhere with `VITE_API_URL`.

## Source map

```
src/
├── App.tsx            routes: "/" (plan list + composer) · "/plans/:id/*" (the plan
│                      shell: rail + Overview/Goals/Agents/Activity + chat + gate +
│                      console dock) · "/settings"
├── lib/
│   ├── api.ts         typed fetch layer over the generated types
│   ├── queries.ts     ALL React Query hooks + useSSEBridge (SSE → cache invalidation,
│   │                  event_id dedup, reconnect → blanket refetch) + the mutation
│   │                  pattern (invalidate on success, toast the error envelope)
│   ├── layout.ts      dagre two-level layout for the goals canvas
│   ├── theme.ts / toast.ts / time.ts
├── store/plannerStore.ts   zustand: SSE connection state, buffered event feed,
│                      agent console lines, toasts — NEVER server state
├── components/        ChatPanel · GatePanel (the two gates) · LifecycleRail (9-phase
│                      stepper) · PlanCanvas/GoalGroupNode/TaskNode · ConsoleDock ·
│                      TopBar · Toaster · ui/ (Button, Card, Dialog, Field, Input,
│                      Select, ConfirmAction — two-step destructive actions)
├── views/             Overview · Goals · Agents · Activity · Plans ·
│                      settings/ (Capabilities, Agents+runtime bindings, Providers/
│                      Models, Projects, Reasoner, Runner — full CRUD)
└── types/
    ├── generated/     openapi-ts output — regenerate, never hand-edit
    └── ui.ts          the HAND-DECLARED plan detail read model (the aggregate
                       document from GET /plans/{id}) — keep in sync with the domain
```

## Rules that keep it coherent

1. **SSE events invalidate, they don't patch.** Event payloads are minimal by backend contract; on any relevant event the affected query keys refetch. Never write server data into zustand.
2. **The chat reply comes from the HTTP response** (`MessageResponse{reply, committed, phase}`) — don't wait on SSE for it.
3. **DTOs come from `types/generated/`** — if a shape is missing, fix the backend schema and regenerate; the one sanctioned exception is `types/ui.ts` (the plan document).
4. **Degrade loudly**: when the stream drops, views dim behind the StaleNotice and reconnection triggers a full refetch — that refetch strategy is *why* the backend doesn't need SSE replay.
