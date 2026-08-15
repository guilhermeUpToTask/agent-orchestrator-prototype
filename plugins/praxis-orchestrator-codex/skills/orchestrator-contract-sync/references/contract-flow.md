# Contract flow

```text
FastAPI routers and Pydantic DTOs
  -> create_app().openapi()
  -> frontend/openapi.json
  -> @hey-api/openapi-ts
  -> frontend/src/types/generated/
  -> frontend API/query consumers
```

- `backend/scripts/export_openapi.py` canonicalizes set-derived defaults. Generation must be byte-stable.
- `frontend/src/types/ui.ts` is handwritten because plan detail is the aggregate document; compare it explicitly with backend serialization.
- Routers return chat replies in HTTP bodies. SSE carries named domain and agent events, not duplicate chat replies.
- Routers do not publish directly to the broker. State transactions write the outbox; the relay publishes.
- SSE is at-least-once. Frontend consumers deduplicate on `event_id`.
- A changed event name or payload affects the outbox producer, relay/API contract, UI event listener, and tests.
