from pydantic import BaseModel


class IAModel(BaseModel):
    id: str
    provider_id: str
    name: str
    # Optional per-model override of the provider's in-flight cap (domain
    # unfreeze #16). None = inherit the provider's value, then the global
    # default. Needed because one model on a key can route to a far smaller
    # upstream pool than its siblings.
    max_inflight: int | None = None
