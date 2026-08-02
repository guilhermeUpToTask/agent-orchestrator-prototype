/**
 * What a fresh install still needs, in the order the pieces depend on each
 * other.
 *
 * `GET /api/readiness` answers WHETHER the install is ready and links to the
 * screen that owns each failing check. It cannot answer what to do first: one
 * `catalog: fail` covers providers, models and agents — three actions across
 * two screens, with no progress until every one of them is done — and nothing
 * says that a reasoner cannot be pointed at a model that does not exist yet.
 *
 * Steps are DERIVED from live catalog state, never stored, so the wizard is
 * re-entrant by construction: an operator who ran `orchestrate seed demo`, or
 * configured half of this by hand, sees those steps already satisfied instead
 * of being asked to redo them.
 */

export type Tier = 'tier0' | 'tier1';

export interface SetupFacts {
  tier: Tier;
  providers: number;
  models: number;
  agents: number;
  /** Agents carrying BOTH a provider and a model — the only kind Tier 1 can run. */
  boundAgents: number;
  hasDefaultAgent: boolean;
  projects: number;
  reasonerMode: string;
  reasonerProviderId: string;
  reasonerModelId: string;
  runnerMode: string;
}

export interface SetupStep {
  id: 'provider' | 'model' | 'agent' | 'default_agent' | 'reasoner' | 'runner' | 'project';
  title: string;
  /** Why this step exists, in terms of what breaks without it. */
  why: string;
  done: boolean;
  /** The settings section that owns it, for operators who prefer the raw panel. */
  section: string;
}

export function setupSteps(facts: SetupFacts): SetupStep[] {
  const tier1 = facts.tier === 'tier1';
  const steps: SetupStep[] = [];

  if (tier1) {
    steps.push({
      id: 'provider',
      title: 'Register a provider',
      why: 'Where real model calls go, and where the encrypted API key lives.',
      done: facts.providers > 0,
      section: '/settings/providers',
    });
    steps.push({
      id: 'model',
      title: 'Add a model',
      why: 'The reasoner and every agent name a model row, not a raw string.',
      done: facts.models > 0,
      section: '/settings/providers',
    });
  }

  steps.push({
    id: 'agent',
    title: tier1 ? 'Create an agent bound to that model' : 'Create an agent',
    why: tier1
      ? 'Unbound, its first attempt fails with a terminal AUTH_ERROR — mid-run, not here.'
      : 'Tasks bind to an agent. Dry-run needs no provider, but it needs someone to bind to.',
    // Tier 1 needs the binding, not merely a row.
    done: tier1 ? facts.boundAgents > 0 : facts.agents > 0,
    section: '/settings/agents',
  });

  steps.push({
    id: 'default_agent',
    title: 'Set the default agent',
    why: 'The fallback when no capability matches. Without it, edits fail NO_DEFAULT_AGENT.',
    done: facts.hasDefaultAgent,
    section: '/settings/agents',
  });

  steps.push({
    id: 'reasoner',
    title: tier1 ? 'Point the reasoner at your model' : 'Keep the reasoner on stub',
    why: tier1
      ? 'Planning runs on a real model only once mode, provider and model all agree.'
      : 'Stub planning is deterministic and free — the right default for a first run.',
    done: tier1
      ? facts.reasonerMode === 'llm'
        && !!facts.reasonerProviderId
        && !!facts.reasonerModelId
      : facts.reasonerMode === 'stub',
    section: '/settings/reasoner',
  });

  steps.push({
    id: 'runner',
    title: tier1 ? 'Switch the agent runtime to real' : 'Keep the agent runtime on dry-run',
    why: tier1
      ? 'Real runs need the CLI the agent is bound to (pi, claude) on PATH.'
      : 'Dry-run exercises the whole lifecycle without spending a token.',
    done: tier1 ? facts.runnerMode === 'real' : facts.runnerMode === 'dry-run',
    section: '/settings/runner',
  });

  steps.push({
    id: 'project',
    title: 'Create a project',
    why: 'A plan reaches your repository through its project — no repo_url, no repository.',
    done: facts.projects > 0,
    section: '/settings/projects',
  });

  return steps;
}

/** The first unsatisfied step, or null when the install is ready. */
export function nextStep(facts: SetupFacts): SetupStep | null {
  return setupSteps(facts).find((step) => !step.done) ?? null;
}
