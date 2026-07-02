from pydantic import BaseModel

from domain.entities.ia_model import IAModel


class ModelProvider(BaseModel):
    id: str
    name: str
    base_url: str
    api_key: str
    models: list[IAModel]

    def add_model(self, model: IAModel):
        self.models.append(model)

    def delete_model(self, model: IAModel):
        self.models.remove(model)

    def get_model(self, model_id: str) -> IAModel | None:
        return next((m for m in self.models if m.id == model_id), None)
