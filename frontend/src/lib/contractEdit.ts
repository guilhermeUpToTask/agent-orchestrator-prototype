import type { ContractCriterion, VerificationStrategy } from '../types/ui';

/** Every field `update_task_contract` accepts, as the editor holds them. */
export interface ContractFormValues {
  objective: string;
  acceptance_criteria: ContractCriterion[];
  verification_strategy: VerificationStrategy;
  allowed_scope: string[];
  forbidden_scope: string[];
  verification_commands: string[];
  goal_criterion_ids: string[];
  required_capabilities: string[];
}

/**
 * The fields whose value actually changed — nothing else may be submitted.
 *
 * `update_task_contract` routes on field PRESENCE, not on field change, and two
 * of those routes are destructive: `objective`/`acceptance_criteria` send the
 * task through `Task.semantic_edit` (revision bump, revision-bound evidence and
 * test bundle invalidated), and `required_capabilities` makes `apply_edit`
 * re-run `match_agent`, replacing a deliberate agent binding. Submitting the
 * whole form therefore turned a one-command repair into a re-authoring plus a
 * rebind — the exact cost un-freeze #17 added `amend_contract` to avoid.
 *
 * The presence-based routing is right for an API client asking for exactly one
 * effect; sending the delta is how a form asks for exactly one effect.
 */
export function changedContractFields(
  loaded: ContractFormValues,
  edited: ContractFormValues,
): Partial<ContractFormValues> {
  const changed: Partial<ContractFormValues> = {};
  if (edited.objective !== loaded.objective) changed.objective = edited.objective;
  if (edited.verification_strategy !== loaded.verification_strategy) {
    changed.verification_strategy = edited.verification_strategy;
  }
  if (!sameCriteria(loaded.acceptance_criteria, edited.acceptance_criteria)) {
    changed.acceptance_criteria = edited.acceptance_criteria;
  }
  if (!sameList(loaded.allowed_scope, edited.allowed_scope)) {
    changed.allowed_scope = edited.allowed_scope;
  }
  if (!sameList(loaded.forbidden_scope, edited.forbidden_scope)) {
    changed.forbidden_scope = edited.forbidden_scope;
  }
  if (!sameList(loaded.verification_commands, edited.verification_commands)) {
    changed.verification_commands = edited.verification_commands;
  }
  if (!sameList(loaded.goal_criterion_ids, edited.goal_criterion_ids)) {
    changed.goal_criterion_ids = edited.goal_criterion_ids;
  }
  if (!sameList(loaded.required_capabilities, edited.required_capabilities)) {
    changed.required_capabilities = edited.required_capabilities;
  }
  return changed;
}

/**
 * The loaded contract in form shape, normalized exactly the way the form's text
 * areas are parsed (trim, drop empty).
 *
 * Comparing a raw stored value against a parsed one would report stray
 * whitespace as an edit the operator never made — and for `objective` or
 * `acceptance_criteria` a phantom edit is not cosmetic: it re-authors the
 * tests. Both sides of the diff must be normalized the same way.
 */
export function contractFormBaseline(
  contract: {
    objective: string;
    acceptance_criteria: ContractCriterion[];
    verification_strategy: VerificationStrategy;
    allowed_scope: string[];
    forbidden_scope?: string[] | null;
    verification_commands: string[];
    goal_criterion_ids: string[];
    required_capabilities?: string[] | null;
  },
  taskCapabilities: string[],
): ContractFormValues {
  return {
    objective: contract.objective.trim(),
    acceptance_criteria: contract.acceptance_criteria
      .map((criterion) => ({
        id: criterion.id.trim(),
        description: criterion.description.trim(),
      }))
      .filter((criterion) => criterion.id && criterion.description),
    verification_strategy: contract.verification_strategy,
    allowed_scope: normalize(contract.allowed_scope),
    forbidden_scope: normalize(contract.forbidden_scope),
    verification_commands: normalize(contract.verification_commands),
    goal_criterion_ids: normalize(contract.goal_criterion_ids),
    required_capabilities: normalize(contract.required_capabilities ?? taskCapabilities),
  };
}

function normalize(values: string[] | null | undefined): string[] {
  return (values ?? []).map((value) => value.trim()).filter(Boolean);
}

/** Order is significant — task position and command order both carry meaning. */
function sameList(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((item, index) => item === right[index]);
}

function sameCriteria(left: ContractCriterion[], right: ContractCriterion[]): boolean {
  return (
    left.length === right.length
    && left.every(
      (item, index) =>
        item.id === right[index].id && item.description === right[index].description,
    )
  );
}
