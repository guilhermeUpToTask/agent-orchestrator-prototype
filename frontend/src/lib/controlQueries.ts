/**
 * src/lib/controlQueries.ts
 *
 * React Query hooks for the control plane. Mutations invalidate the relevant
 * caches and surface errors as toasts (rendering the envelope's code/message +
 * request_id via errorDetail). Switching the active project refetches all
 * scoped data.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  activateProject,
  addModel,
  createProject,
  deleteAgentDefinition,
  deleteProject,
  deleteProvider,
  listAgentDefinitions,
  listProjects,
  listProviders,
  listSecretRefs,
  registerAgentDefinition,
  registerProvider,
  storeSecret,
} from './controlPlane';
import { errorDetail, toast } from './toast';
import { useProjectStore } from '../store/projectStore';
import type {
  AgentDefinitionCreate,
  ModelCreate,
  ProjectCreate,
  ProviderCreate,
  SecretCreate,
} from '../types/control';

export const controlKeys = {
  projects: ['cp', 'projects'] as const,
  providers: ['cp', 'providers'] as const,
  agentDefs: ['cp', 'agent-definitions'] as const,
  secrets: ['cp', 'secrets'] as const,
};

// ─── Projects ──────────────────────────────────────────────────────────────────

export const useProjects = () =>
  useQuery({ queryKey: controlKeys.projects, queryFn: listProjects });

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProjectCreate) => createProject(body),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: controlKeys.projects });
      toast.success('Project created', p.id);
    },
    onError: (e) => toast.error('Create project failed', errorDetail(e)),
  });
}

export function useDeleteProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, cascade }: { id: string; cascade?: boolean }) =>
      deleteProject(id, cascade),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: controlKeys.projects });
      toast.success('Project deleted');
    },
    onError: (e) => toast.error('Delete project failed', errorDetail(e)),
  });
}

export function useActivateProject() {
  const qc = useQueryClient();
  const setActiveProjectId = useProjectStore((s) => s.setActiveProjectId);
  return useMutation({
    mutationFn: (id: string) => activateProject(id),
    onSuccess: (p) => {
      setActiveProjectId(p.id);
      // Project context changed — refetch all scoped server state.
      qc.invalidateQueries();
      toast.success('Active project', p.id);
    },
    onError: (e) => toast.error('Switch project failed', errorDetail(e)),
  });
}

// ─── Providers + models ─────────────────────────────────────────────────────────

export const useProviders = () =>
  useQuery({ queryKey: controlKeys.providers, queryFn: listProviders });

export function useRegisterProvider() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProviderCreate) => registerProvider(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: controlKeys.providers });
      qc.invalidateQueries({ queryKey: controlKeys.secrets });
      toast.success('Provider registered');
    },
    onError: (e) => toast.error('Register provider failed', errorDetail(e)),
  });
}

export function useAddModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ providerId, body }: { providerId: string; body: ModelCreate }) =>
      addModel(providerId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: controlKeys.providers });
      toast.success('Model added');
    },
    onError: (e) => toast.error('Add model failed', errorDetail(e)),
  });
}

export function useDeleteProvider() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteProvider(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: controlKeys.providers });
      toast.success('Provider deleted');
    },
    onError: (e) => toast.error('Delete provider failed', errorDetail(e)),
  });
}

// ─── Agent definitions ──────────────────────────────────────────────────────────

export const useAgentDefinitions = () =>
  useQuery({ queryKey: controlKeys.agentDefs, queryFn: listAgentDefinitions });

export function useRegisterAgentDefinition() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AgentDefinitionCreate) => registerAgentDefinition(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: controlKeys.agentDefs });
      toast.success('Agent registered');
    },
    onError: (e) => toast.error('Register agent failed', errorDetail(e)),
  });
}

export function useDeleteAgentDefinition() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteAgentDefinition(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: controlKeys.agentDefs });
      toast.success('Agent deleted');
    },
    onError: (e) => toast.error('Delete agent failed', errorDetail(e)),
  });
}

// ─── Secrets ────────────────────────────────────────────────────────────────────

export const useSecretRefs = () =>
  useQuery({ queryKey: controlKeys.secrets, queryFn: listSecretRefs });

export function useStoreSecret() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SecretCreate) => storeSecret(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: controlKeys.secrets });
      toast.success('Secret stored');
    },
    onError: (e) => toast.error('Store secret failed', errorDetail(e)),
  });
}
