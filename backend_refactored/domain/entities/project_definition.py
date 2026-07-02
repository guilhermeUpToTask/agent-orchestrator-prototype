from pydantic import BaseModel


class ProjectDefinition(BaseModel):
    id: str
    name: str
    repo_url: str | None
