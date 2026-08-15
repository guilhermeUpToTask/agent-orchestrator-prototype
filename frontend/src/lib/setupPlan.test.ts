import { describe, expect, it } from 'vitest';
import { nextStep, setupSteps, type SetupFacts } from './setupPlan';

/**
 * A fresh install answers `catalog: fail` and links to "Providers & models".
 * That is where a new operator gets stuck: the checklist names what is wrong,
 * never the ORDER, and one failing check ("0 capabilities · 0 agents · 0
 * provider/model") hides three separate actions across two screens with no
 * progress until all of them are done.
 *
 * These steps are derived from live catalog state rather than stored, so the
 * wizard is re-entrant: an operator who set half of this up by hand, or by
 * `praxis seed demo`, sees those steps already satisfied.
 */
const EMPTY: SetupFacts = {
  tier: 'tier0',
  providers: 0,
  models: 0,
  agents: 0,
  boundAgents: 0,
  hasDefaultAgent: false,
  projects: 0,
  reasonerMode: 'stub',
  reasonerProviderId: '',
  reasonerModelId: '',
  runnerMode: 'dry-run',
};

const ids = (facts: SetupFacts) => setupSteps(facts).map((step) => step.id);
const done = (facts: SetupFacts) =>
  setupSteps(facts).filter((step) => step.done).map((step) => step.id);

describe('Tier 0 — the free path', () => {
  it('never asks for a provider or an API key', () => {
    /** The trap this exists to remove: in stub + dry-run neither the reasoner
     *  nor the runner resolves a provider row, so demanding one sends a new
     *  operator looking for an API key they do not need. */
    expect(ids(EMPTY)).not.toContain('provider');
    expect(ids(EMPTY)).not.toContain('model');
  });

  it('asks for an agent, a default binding and a project', () => {
    expect(ids(EMPTY)).toEqual(['agent', 'default_agent', 'reasoner', 'runner', 'project']);
  });

  it('counts the modes a fresh install already has as done', () => {
    // stub + dry-run are the shipped defaults — a wizard that made you set
    // them anyway would be theatre.
    expect(done(EMPTY)).toEqual(['reasoner', 'runner']);
  });

  it('is complete once an agent, a default and a project exist', () => {
    const ready: SetupFacts = { ...EMPTY, agents: 1, hasDefaultAgent: true, projects: 1 };

    expect(nextStep(ready)).toBeNull();
    expect(setupSteps(ready).every((step) => step.done)).toBe(true);
  });
});

describe('Tier 1 — real models', () => {
  const tier1: SetupFacts = { ...EMPTY, tier: 'tier1', reasonerMode: 'stub', runnerMode: 'dry-run' };

  it('adds the provider and model steps in dependency order', () => {
    expect(ids(tier1)).toEqual([
      'provider',
      'model',
      'agent',
      'default_agent',
      'reasoner',
      'runner',
      'project',
    ]);
  });

  it('does not count the stub reasoner as configured', () => {
    expect(done(tier1)).toEqual([]);
  });

  it('needs the reasoner pointed at a real provider AND model', () => {
    const half: SetupFacts = {
      ...tier1,
      providers: 1,
      models: 1,
      reasonerMode: 'llm',
      reasonerProviderId: 'openrouter',
      reasonerModelId: '',
    };

    expect(done(half)).not.toContain('reasoner');
  });

  it('requires an agent BOUND to a provider and model, not merely present', () => {
    /** An unbound agent fails at the first real attempt with AUTH_ERROR, which
     *  is a terminal failure an operator meets mid-run instead of at setup. */
    const unbound: SetupFacts = { ...tier1, providers: 1, models: 1, agents: 1, boundAgents: 0 };

    expect(done(unbound)).not.toContain('agent');
    expect(nextStep(unbound)?.id).toBe('agent');
  });
});

describe('the next step', () => {
  it('is the first unsatisfied one, in dependency order', () => {
    expect(nextStep(EMPTY)?.id).toBe('agent');
    expect(nextStep({ ...EMPTY, agents: 1 })?.id).toBe('default_agent');
    expect(nextStep({ ...EMPTY, agents: 1, hasDefaultAgent: true })?.id).toBe('project');
  });

  it('is null when everything is satisfied', () => {
    const ready: SetupFacts = { ...EMPTY, agents: 1, hasDefaultAgent: true, projects: 1 };

    expect(nextStep(ready)).toBeNull();
  });

  it('skips a step an operator already satisfied by hand or by seed demo', () => {
    /** `praxis seed demo` sets up most of this; the wizard must not insist
     *  on redoing it. */
    const seeded: SetupFacts = {
      ...EMPTY,
      tier: 'tier1',
      providers: 1,
      models: 1,
      agents: 1,
      boundAgents: 1,
      hasDefaultAgent: true,
      reasonerMode: 'llm',
      reasonerProviderId: 'openrouter',
      reasonerModelId: 'nemotron',
      runnerMode: 'real',
    };

    expect(nextStep(seeded)?.id).toBe('project');
  });
});
