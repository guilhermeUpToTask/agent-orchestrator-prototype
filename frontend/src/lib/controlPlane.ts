/**
 * src/lib/controlPlane.ts
 *
 * HTTP client for the SQLite control-plane API (projects, providers, models,
 * agent definitions, secrets). Errors carry the server's enveloped body so the
 * toast layer (errorDetail) can surface code/message/request_id.
 */

import type {
  AgentDefinition,
  AgentDefinitionCreate,
  ModelCreate,
  Project,
  ProjectCreate,
  Provider,
  ProviderCreate,
  SecretCreate,
  SecretRef,
} from '../types/control';

const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${method} ${path} → ${res.status}: ${text}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

// ─── Projects ──────────────────────────────────────────────────────────────────

export const listProjects = (): Promise<Project[]> => request('GET', '/api/projects');
export const createProject = (body: ProjectCreate): Promise<Project> =>
  request('POST', '/api/projects', body);
export const activateProject = (id: string): Promise<Project> =>
  request('POST', `/api/projects/${encodeURIComponent(id)}/activate`);
export const deleteProject = (id: string, cascade = false): Promise<void> =>
  request('DELETE', `/api/projects/${encodeURIComponent(id)}?cascade=${cascade}`);

// ─── Providers + models ─────────────────────────────────────────────────────────

export const listProviders = (): Promise<Provider[]> => request('GET', '/api/providers');
export const registerProvider = (body: ProviderCreate): Promise<Provider> =>
  request('POST', '/api/providers', body);
export const addModel = (providerId: string, body: ModelCreate): Promise<Provider> =>
  request('POST', `/api/providers/${encodeURIComponent(providerId)}/models`, body);
export const deleteProvider = (id: string): Promise<void> =>
  request('DELETE', `/api/providers/${encodeURIComponent(id)}`);

// ─── Agent definitions ──────────────────────────────────────────────────────────

export const listAgentDefinitions = (): Promise<AgentDefinition[]> =>
  request('GET', '/api/agent-definitions');
export const registerAgentDefinition = (
  body: AgentDefinitionCreate,
): Promise<AgentDefinition> => request('POST', '/api/agent-definitions', body);
export const deleteAgentDefinition = (id: string): Promise<void> =>
  request('DELETE', `/api/agent-definitions/${encodeURIComponent(id)}`);

// ─── Secrets ────────────────────────────────────────────────────────────────────

export const listSecretRefs = (): Promise<SecretRef[]> => request('GET', '/api/secrets');
export const storeSecret = (body: SecretCreate): Promise<SecretRef> =>
  request('POST', '/api/secrets', body);
