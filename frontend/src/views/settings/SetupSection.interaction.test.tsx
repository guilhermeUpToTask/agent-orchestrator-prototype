import React from 'react';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupSection } from './SetupSection';

/**
 * What server-rendered markup cannot answer: is the button wired to the
 * mutation it claims, with the body the API expects?
 *
 * Every other frontend test renders to a string. That proves what a component
 * shows for given props and nothing about what it DOES — which is most of what
 * a setup screen is. These press the actual buttons.
 */
const mocks = vi.hoisted(() => ({
  providers: [] as unknown[],
  models: [] as unknown[],
  agents: [] as unknown[],
  projects: [] as unknown[],
  defaultAgent: null as unknown,
  config: {} as Record<string, string>,
  createProvider: vi.fn(),
  createModel: vi.fn(),
  createAgent: vi.fn(),
  setDefaultAgent: vi.fn(),
  createProject: vi.fn(),
  // Typed so the ORDER assertion below can read `.key` off the calls.
  setConfigKey: vi.fn((_body: { key: string; value: string }) => undefined),
  setConfigKeyAsync: vi.fn(async (_body: { key: string; value: string }) => undefined),
}));

vi.mock('../../lib/queries', () => ({
  useProviders: () => ({ data: mocks.providers }),
  useModels: () => ({ data: mocks.models }),
  useAgents: () => ({ data: mocks.agents }),
  useProjects: () => ({ data: mocks.projects }),
  useDefaultAgent: () => ({ data: mocks.defaultAgent }),
  useConfigScope: () => ({ data: mocks.config }),
  useCreateProvider: () => ({ mutate: mocks.createProvider, isPending: false }),
  useCreateModel: () => ({ mutate: mocks.createModel, isPending: false }),
  useCreateAgent: () => ({ mutate: mocks.createAgent, isPending: false }),
  useSetDefaultAgent: () => ({ mutate: mocks.setDefaultAgent, isPending: false }),
  useCreateProject: () => ({ mutate: mocks.createProject, isPending: false }),
  useSetConfigKey: () => ({
    mutate: mocks.setConfigKey,
    mutateAsync: mocks.setConfigKeyAsync,
    isPending: false,
  }),
}));

function show() {
  return render(
    <MemoryRouter>
      <SetupSection />
    </MemoryRouter>,
  );
}

describe('the setup wizard actually writes', () => {
  beforeEach(() => {
    mocks.providers = [];
    mocks.models = [];
    mocks.agents = [];
    mocks.projects = [];
    mocks.defaultAgent = null;
    mocks.config = {};
    vi.clearAllMocks();
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('creates a Tier 0 agent with the generalist defaults and no provider binding', async () => {
    show();

    await userEvent.click(screen.getByRole('button', { name: /create agent/i }));

    expect(mocks.createAgent).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'dev-agent', role: 'implementer', model_role: 'smart' }),
    );
    // Tier 0 binds nothing: dry-run resolves no provider row.
    const body = mocks.createAgent.mock.calls[0][0];
    expect(body).not.toHaveProperty('provider_id');
  });

  it('sets the default agent to the one chosen', async () => {
    mocks.agents = [
      { id: 'a1', name: 'first', provider_id: null, model_id: null },
      { id: 'a2', name: 'second', provider_id: null, model_id: null },
    ];
    show();

    await userEvent.selectOptions(screen.getByLabelText(/default agent/i), 'a2');
    await userEvent.click(screen.getByRole('button', { name: /set as default/i }));

    expect(mocks.setDefaultAgent).toHaveBeenCalledWith('a2');
  });

  it('creates a project with the repository the operator typed', async () => {
    mocks.agents = [{ id: 'a1', name: 'dev', provider_id: null, model_id: null }];
    mocks.defaultAgent = { agent_id: 'a1' };
    show();

    await userEvent.type(screen.getByLabelText(/^name$/i), 'my-project');
    await userEvent.type(screen.getByLabelText(/repository/i), '/home/me/code');
    await userEvent.click(screen.getByRole('button', { name: /create project/i }));

    expect(mocks.createProject).toHaveBeenCalledWith({
      name: 'my-project',
      repo_url: '/home/me/code',
    });
  });

  it('sends no repo_url when the field is left blank, so the project gets a scratch repo', async () => {
    mocks.agents = [{ id: 'a1', name: 'dev', provider_id: null, model_id: null }];
    mocks.defaultAgent = { agent_id: 'a1' };
    show();

    await userEvent.type(screen.getByLabelText(/^name$/i), 'scratch');
    await userEvent.click(screen.getByRole('button', { name: /create project/i }));

    expect(mocks.createProject).toHaveBeenCalledWith({ name: 'scratch', repo_url: null });
  });

  it('refuses to submit a step whose required field is empty', async () => {
    mocks.agents = [{ id: 'a1', name: 'dev', provider_id: null, model_id: null }];
    mocks.defaultAgent = { agent_id: 'a1' };
    show();

    // `toBeDisabled` is a jest-dom matcher; asserting the DOM property keeps
    // this suite to the two libraries it already has.
    const submit = screen.getByRole('button', { name: /create project/i }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);

    await userEvent.click(submit);
    expect(mocks.createProject).not.toHaveBeenCalled();
  });
});

describe('Tier 1 ordering', () => {
  beforeEach(() => {
    mocks.providers = [{ id: 'openrouter', name: 'openrouter' }];
    mocks.models = [{ id: 'm1', name: 'nemotron', provider_id: 'openrouter' }];
    mocks.agents = [{ id: 'a1', name: 'dev', provider_id: 'openrouter', model_id: 'm1' }];
    mocks.defaultAgent = { agent_id: 'a1' };
    mocks.projects = [{ id: 'p1', name: 'demo' }];
    mocks.config = { 'reasoner.mode': 'stub', 'agent_runner.mode': 'dry-run' };
    vi.clearAllMocks();
  });
  afterEach(() => cleanup());

  it('writes provider and model BEFORE mode, so llm never points at nothing', async () => {
    /** The failure this ordering prevents: `reasoner.mode=llm` written first
     *  leaves the config briefly pointing at a provider it has not been given,
     *  and a worker resolving in that window fails REASONER_CONFIG_INVALID. */
    show();
    await userEvent.selectOptions(screen.getByLabelText(/^tier$/i), 'tier1');

    await userEvent.click(screen.getByRole('button', { name: /plan with this model/i }));

    const keys = mocks.setConfigKeyAsync.mock.calls.map((call) => call[0].key);
    expect(keys).toEqual([
      'reasoner.provider_id',
      'reasoner.model_id',
      'reasoner.mode',
    ]);
  });

  it('switches the runtime to real for Tier 1', async () => {
    mocks.config = {
      'reasoner.mode': 'llm',
      'reasoner.provider_id': 'openrouter',
      'reasoner.model_id': 'm1',
      'agent_runner.mode': 'dry-run',
    };
    show();
    await userEvent.selectOptions(screen.getByLabelText(/^tier$/i), 'tier1');

    await userEvent.click(screen.getByRole('button', { name: /set agent runtime to real/i }));

    expect(mocks.setConfigKey).toHaveBeenCalledWith({ key: 'agent_runner.mode', value: 'real' });
  });
});
