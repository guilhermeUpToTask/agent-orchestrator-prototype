from pydantic import BaseModel

from src.domain.entities.ia_model import IAModel


class ModelProvider(BaseModel):
    id: str
    name: str
    base_url: str
    # Reference (URI) into the secret store — NEVER the plaintext key. Resolution
    # happens in infra at the single decryption point; keys never enter the domain.
    api_key_ref: str
    models: list[IAModel]
    # --- capacity metadata (domain unfreeze #16) ---
    # How many attempts may be in flight against this provider at once. None =
    # fall back to the operator's global default. This belongs to the PROVIDER,
    # not the orchestrator: a paid tier, a free aggregator, and a local
    # single-GPU server have wildly different ceilings, and one global number
    # would either throttle the paid tier to free-tier levels or over-drive the
    # local one.
    max_inflight: int | None = None
    # How this provider's limits are structured — `per_model` (default) or
    # `endpoint_wide`. An aggregator routes each model to its own upstream pool,
    # so its concurrency limits are per-model while billing is account-wide; a
    # single-endpoint deployment shares one pool across every model it serves.
    # Declared as provider DATA so no handler ever branches on a provider name.
    capacity_scope: str | None = None

    def add_model(self, model: IAModel) -> None:
        self.models.append(model)

    def delete_model(self, model: IAModel) -> None:
        self.models.remove(model)

    def get_model(self, model_id: str) -> IAModel | None:
        return next((m for m in self.models if m.id == model_id), None)
