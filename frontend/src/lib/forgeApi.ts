/**
 * src/lib/forgeApi.ts — PR-window HTTP client.
 */

import type {
  CommitGraph,
  ForgeCapabilities,
  PullRequest,
} from '../types/forge';

const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`GET ${path} → ${res.status}: ${text}`);
  }
  return res.json();
}

export const fetchCommitGraph = (projectId: string, branch?: string, limit = 200): Promise<CommitGraph> =>
  get(`/api/projects/${encodeURIComponent(projectId)}/commit-graph?limit=${limit}${branch ? `&branch=${encodeURIComponent(branch)}` : ''}`);

export const fetchForgeCapabilities = (projectId: string): Promise<ForgeCapabilities> =>
  get(`/api/projects/${encodeURIComponent(projectId)}/forge-capabilities`);

export const fetchPullRequests = (projectId: string): Promise<PullRequest[]> =>
  get(`/api/projects/${encodeURIComponent(projectId)}/prs`);
