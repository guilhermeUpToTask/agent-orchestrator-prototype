import type { Goal, Plan } from '../types/ui';

/** Canonical live roadmap; root goals exist only for migrated compatibility rows. */
export function currentPlanGoals(plan: Plan | null | undefined): Goal[] {
  return plan?.active_cycle?.goals ?? plan?.goals ?? [];
}

export type ConversationMode = 'discovery' | 'replanning' | null;

/** Chat availability follows the server's activity, not its legacy phase projection. */
export function conversationMode(plan: Plan | null | undefined): ConversationMode {
  if (plan?.activity === 'intent_discovery') return 'discovery';
  if (plan?.activity === 'replan_discovery') return 'replanning';
  return null;
}

export function isLegacyPlan(plan: Plan | null | undefined): boolean {
  return plan?.legacy_phase != null;
}
