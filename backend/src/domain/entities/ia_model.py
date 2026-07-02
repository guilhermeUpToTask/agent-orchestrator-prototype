from pydantic import BaseModel


class IAModel(BaseModel):
    id: str
    provider_id: str
    name: str
