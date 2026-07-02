from __future__ import annotations

from typing import Any

from domain.entities.ia_model import IAModel
from domain.entities.model_provider import ModelProvider
from domain.factories.identity import new_id


class ModelFactory:
    @staticmethod
    def create(name: str, provider_id: str) -> IAModel:
        return IAModel(id=new_id(), name=name, provider_id=provider_id)

    @staticmethod
    def reconstruct(data: dict[str, Any]) -> IAModel:
        return IAModel.model_validate(data)


class ProviderFactory:
    @staticmethod
    def create(name: str, base_url: str, api_key: str) -> ModelProvider:
        return ModelProvider(id=new_id(), name=name, base_url=base_url, api_key=api_key, models=[])

    @staticmethod
    def reconstruct(data: dict[str, Any]) -> ModelProvider:
        return ModelProvider.model_validate(data)
