import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Plan } from '../types/ui';
import { Overview } from './Overview';

const mocks = vi.hoisted(() => ({ plan: null as unknown as Plan }));

vi.mock('../lib/queries', () => ({
  useAgents: () => ({ data: [] }),
  useBindProject: () => ({ mutate: vi.fn(), isPending: false }),
  useCycleEvidence: () => ({ data: undefined, isLoading: false, error: null }),
  usePlan: () => ({ data: mocks.plan, isLoading: false, error: null, refetch: vi.fn() }),
  usePlanningArtifacts: () => ({ data: [] }),
  useProjects: () => ({ data: [] }),
  useReplanMidRunning: () => ({ mutate: vi.fn(), isPending: false }),
  useRetryPlanningStage: () => ({ mutate: vi.fn(), isPending: false }),
  useRetryTask: () => ({ mutate: vi.fn(), isPending: false }),
}));

function plan(): Plan {
  const blockedGoal = {
    id: 'goal-blocked',
    name: 'Blocked goal',
    position: 0,
    description: '',
    status: 'pending' as const,
    depends_on: [],
    tasks: [],
  };
  return {
    id: 'plan-1',
    project_id: 'project-1',
    status: 'running',
    status_reason: { kind: 'activity', code: null, message: 'Independent work continues.' },
    activity: 'goal_execution',
    current_goal_id: null,
    current_task_id: null,
    tdd_stage: null,
    legal_actions: [],
    action_endpoints: {},
    pause_requested: false,
    active_run: null,
    worker_lease: null,
    provider_waiting: {
      provider_id: 'provider-1',
      model_id: 'model-1',
      runtime: 'pi',
      limit_scope: 'requests',
      retry_at: new Date(Date.now() + 30_000).toISOString(),
      since: new Date().toISOString(),
      failure_count: 2,
      safe_message: 'Provider capacity is recovering.',
      needs_attention: false,
    },
    planning_operation: null,
    planning_progress: null,
    active_cycle: {
      id: 'cycle-1',
      intent_proposal_id: 'intent-1',
      draft_id: 'draft-1',
      status: 'active',
      goals: [blockedGoal],
      started_at: new Date().toISOString(),
      completed_at: null,
      superseded_at: null,
      cancelled_at: null,
      evidence_refs: [],
      output_disposition: null,
      output_reference: null,
    },
    pending_gate: null,
    block: null,
    goal_blocks: {
      'goal-blocked': {
        id: 'block-1',
        kind: 'contract',
        explanation: 'Repair the blocked goal contract.',
        stage: 'goal_contract',
        goal_id: 'goal-blocked',
        task_id: null,
        task_revision: null,
        run_id: null,
        evidence_refs: [],
        legal_resolutions: ['retry_stage'],
        requires_human: true,
        created_at: new Date().toISOString(),
        resolved_at: null,
        resolution: null,
      },
    },
    cycles: [],
    intent_proposal: null,
    cycle_draft: null,
    brief: 'Phase 5 truth',
    legacy_phase: null,
    phase: 'running',
    iteration: 0,
    version: 1,
    goals: [],
    paused: false,
    paused_reason: null,
  };
}

describe('Overview Phase 5 state separation', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation((message: unknown) => {
      if (!String(message).includes('useLayoutEffect does nothing on the server')) {
        throw new Error(String(message));
      }
    });
  });

  afterEach(() => vi.restoreAllMocks());

  it('renders a per-goal human block separately from automatic provider recovery', () => {
    mocks.plan = plan();

    const html = renderToStaticMarkup(
      <MemoryRouter initialEntries={['/plans/plan-1']}>
        <Routes>
          <Route path="/plans/:planId" element={<Overview />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(html).toContain('Repair the blocked goal contract.');
    expect(html).toContain('Retry work');
    expect(html).toContain('Recovering automatically');
    expect(html).toContain('Provider capacity is recovering.');
    expect(html).toContain('retrying automatically');
  });
});
