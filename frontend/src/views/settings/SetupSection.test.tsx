import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupSection } from './SetupSection';

/**
 * The wizard reads live catalog state, so what it shows IS the assertion: a
 * fresh install must be told to create an agent, and a seeded one must not be
 * asked to redo work it already has.
 */
const mocks = vi.hoisted(() => ({
  providers: [] as unknown[],
  models: [] as unknown[],
  agents: [] as unknown[],
  projects: [] as unknown[],
  defaultAgent: null as unknown,
  config: {} as Record<string, string>,
}));

vi.mock('../../lib/queries', () => ({
  useProviders: () => ({ data: mocks.providers }),
  useModels: () => ({ data: mocks.models }),
  useAgents: () => ({ data: mocks.agents }),
  useProjects: () => ({ data: mocks.projects }),
  useDefaultAgent: () => ({ data: mocks.defaultAgent }),
  useConfigScope: () => ({ data: mocks.config }),
  useCreateProvider: () => ({ mutate: vi.fn(), isPending: false }),
  useCreateModel: () => ({ mutate: vi.fn(), isPending: false }),
  useCreateAgent: () => ({ mutate: vi.fn(), isPending: false }),
  useSetDefaultAgent: () => ({ mutate: vi.fn(), isPending: false }),
  useCreateProject: () => ({ mutate: vi.fn(), isPending: false }),
  useSetConfigKey: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
}));

function render(): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <SetupSection />
    </MemoryRouter>,
  );
}

describe('SetupSection', () => {
  beforeEach(() => {
    mocks.providers = [];
    mocks.models = [];
    mocks.agents = [];
    mocks.projects = [];
    mocks.defaultAgent = null;
    mocks.config = {};
    vi.spyOn(console, 'error').mockImplementation((message: unknown) => {
      if (!String(message).includes('useLayoutEffect does nothing on the server')) {
        throw new Error(String(message));
      }
    });
  });
  afterEach(() => vi.restoreAllMocks());

  it('starts a fresh install on the agent step, not on an API key', () => {
    const html = render();

    expect(html).toContain('Next: Create an agent');
    // Tier 0 is the default, and it needs no provider at all.
    expect(html).not.toContain('Register a provider');
    expect(html).toContain('2 / 5'); // stub + dry-run already satisfied
  });

  it('shows every step with the reason it exists', () => {
    const html = render();

    expect(html).toContain('Set the default agent');
    expect(html).toContain('NO_DEFAULT_AGENT');
    expect(html).toContain('Create a project');
  });

  it('congratulates a finished setup and points at the plans screen', () => {
    mocks.agents = [{ id: 'a1', name: 'dev', provider_id: null, model_id: null }];
    mocks.defaultAgent = { agent_id: 'a1' };
    mocks.projects = [{ id: 'p1', name: 'demo' }];

    const html = render();

    expect(html).toContain('Setup complete');
    expect(html).toContain('Go to plans');
    expect(html).toContain('5 / 5');
  });
});
