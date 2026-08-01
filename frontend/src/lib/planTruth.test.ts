import { describe, expect, it } from 'vitest';
import { conversationMode, currentPlanGoals, isLegacyPlan } from './planTruth';
import type { Goal, Plan } from '../types/ui';

const rootGoal = { id: 'root' } as Goal;
const cycleGoal = { id: 'cycle' } as Goal;

function plan(fields: Partial<Plan>): Plan {
  return {
    goals: [rootGoal],
    cycles: [],
    active_cycle: null,
    activity: 'idle',
    legacy_phase: null,
    ...fields,
  } as Plan;
}

describe('canonical Phase 5 plan truth', () => {
  it('uses active-cycle goals instead of the compatibility root goals', () => {
    const value = plan({
      active_cycle: { goals: [cycleGoal] } as Plan['active_cycle'],
    });

    expect(currentPlanGoals(value)).toEqual([cycleGoal]);
  });

  it('routes chat from activity and never from a projected phase', () => {
    expect(conversationMode(plan({ activity: 'intent_discovery', phase: 'running' }))).toBe('discovery');
    expect(conversationMode(plan({ activity: 'replan_discovery', phase: 'done' }))).toBe('replanning');
    expect(conversationMode(plan({ activity: 'goal_execution', phase: 'discovery' }))).toBeNull();
  });

  it('only marks explicit compatibility rows as legacy', () => {
    expect(isLegacyPlan(plan({ legacy_phase: 'review' }))).toBe(true);
    expect(isLegacyPlan(plan({ legacy_phase: null, phase: 'review' }))).toBe(false);
  });
});
