"""SecretRef — a reference (URI) to a secret in the secret store.

Infra-local on purpose: nothing in domain/app touches secrets; the domain holds
only the opaque ``api_key_ref`` string. The plaintext is never held here and is
resolved exclusively by the secret store adapter.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator


class SecretRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    uri: str

    @field_validator("uri")
    @classmethod
    def _must_be_secret_uri(cls, v: str) -> str:
        if not v.startswith("secret://"):
            raise ValueError("SecretRef must start with 'secret://'")
        return v

    @classmethod
    def for_provider(cls, provider_id: str) -> "SecretRef":
        """Canonical ref for a provider's API key."""
        return cls(uri=f"secret://provider/{provider_id}")

    @classmethod
    def for_forge(cls, project_id: str) -> "SecretRef":
        """Canonical ref for one project's forge token.

        Per project rather than global: two projects can live on different
        accounts, and one credential spanning every project is the wrong blast
        radius for a tool that runs unsandboxed agents on your own machine.
        """
        return cls(uri=f"secret://forge/{project_id}")
