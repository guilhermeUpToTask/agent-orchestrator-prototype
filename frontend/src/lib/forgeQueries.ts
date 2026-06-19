/**
 * src/lib/forgeQueries.ts — React Query hooks for the PR window, scoped to the
 * active project. Disabled until a project is active.
 */

import { useQuery } from '@tanstack/react-query';

import { fetchCommitGraph, fetchForgeCapabilities, fetchPullRequests } from './forgeApi';
import { useProjectStore } from '../store/projectStore';

export const forgeKeys = {
  graph: (id: string) => ['forge', 'graph', id] as const,
  prs: (id: string) => ['forge', 'prs', id] as const,
  caps: (id: string) => ['forge', 'caps', id] as const,
};

export function useCommitGraph() {
  const id = useProjectStore((s) => s.activeProjectId);
  return useQuery({
    queryKey: forgeKeys.graph(id ?? ''),
    queryFn: () => fetchCommitGraph(id as string),
    enabled: !!id,
  });
}

export function usePullRequests() {
  const id = useProjectStore((s) => s.activeProjectId);
  return useQuery({
    queryKey: forgeKeys.prs(id ?? ''),
    queryFn: () => fetchPullRequests(id as string),
    enabled: !!id,
  });
}

export function useForgeCapabilities() {
  const id = useProjectStore((s) => s.activeProjectId);
  return useQuery({
    queryKey: forgeKeys.caps(id ?? ''),
    queryFn: () => fetchForgeCapabilities(id as string),
    enabled: !!id,
  });
}
