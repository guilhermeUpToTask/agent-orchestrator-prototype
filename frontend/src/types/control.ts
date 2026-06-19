// src/types/control.ts
// Control-plane DTO shapes (projects, providers, models, agent definitions,
// secrets). These mirror src/api/schemas/control.py.
//
// NOTE: per the roadmap these should eventually be replaced by openapi-ts
// generated types (`npm run generate:api`); they are hand-written here until
// the control-plane routes are included in the exported OpenAPI schema.

export type ProviderKind = 'anthropic' | 'gemini' | 'openrouter' | 'openai';

export interface Project {
  id: string;
  name: string;
  repo_url: string;
  default_branch: string;
  has_github_token: boolean;
  state_version: number;
}

export interface ProjectCreate {
  name: string;
  repo_url: string;
  default_branch?: string;
  github_token?: string | null;
  project_id?: string | null;
}

export interface RegisteredModel {
  model_id: string;
  display_name: string;
  capabilities: string[];
}

export interface Provider {
  id: string;
  kind: ProviderKind;
  base_url: string | null;
  default_model: string | null;
  models: RegisteredModel[];
  state_version: number;
}

export interface ProviderCreate {
  id: string;
  kind: ProviderKind;
  api_key: string;
  base_url?: string | null;
  default_model?: string | null;
}

export interface ModelCreate {
  model_id: string;
  display_name?: string | null;
  capabilities?: string[];
}

export interface AgentDefinition {
  id: string;
  name: string;
  runtime_type: string;
  provider_id: string;
  model_id: string;
  capabilities: string[];
  state_version: number;
}

export interface AgentDefinitionCreate {
  id: string;
  name: string;
  runtime_type: string;
  provider_id: string;
  model_id: string;
  capabilities?: string[];
}

export interface SecretRef {
  uri: string;
  is_set: boolean;
}

export interface SecretCreate {
  uri: string;
  value: string;
}
