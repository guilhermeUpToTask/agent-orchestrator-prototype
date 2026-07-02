from __future__ import annotations

from typing import Any

from src.domain.entities.ia_model import IAModel
from src.domain.entities.model_provider import ModelProvider
from src.domain.factories.identity import new_id


class ModelFactory:
    @staticmethod
    def create(name: str, provider_id: str) -> IAModel:
        return IAModel(id=new_id(), name=name, provider_id=provider_id)

    @staticmethod
    def reconstruct(data: dict[str, Any]) -> IAModel:
        return IAModel.model_validate(data)


class ProviderFactory:
    @staticmethod
    def create(name: str, base_url: str, api_key_ref: str) -> ModelProvider:
        return ModelProvider(
            id=new_id(), name=name, base_url=base_url, api_key_ref=api_key_ref, models=[]
        )

    @staticmethod
    def reconstruct(data: dict[str, Any]) -> ModelProvider:
        return ModelProvider.model_validate(data)
