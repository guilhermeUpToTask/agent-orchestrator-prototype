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

    def add_model(self, model: IAModel) -> None:
        self.models.append(model)

    def delete_model(self, model: IAModel) -> None:
        self.models.remove(model)

    def get_model(self, model_id: str) -> IAModel | None:
        return next((m for m in self.models if m.id == model_id), None)
