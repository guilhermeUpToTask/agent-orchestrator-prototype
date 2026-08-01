import { describe, expect, it } from 'vitest';
import {
  changedContractFields,
  contractFormBaseline,
  type ContractFormValues,
} from './contractEdit';

/**
 * `update_task_contract` routes on field PRESENCE, not field change:
 * `acceptance_criteria`/`objective` send the task through `semantic_edit`
 * (revision bump, revision-bound evidence and test bundle invalidated), and
 * `required_capabilities` makes `apply_edit` re-run `match_agent`. So a form
 * that submits everything turns a one-command repair into a re-authoring plus
 * an agent rebind. Only the fields the operator actually changed may travel.
 */
const loaded: ContractFormValues = {
  objective: 'implement greet',
  acceptance_criteria: [{ id: 't-1', description: 'greets' }],
  verification_strategy: 'tdd',
  allowed_scope: ['src/happy_path/', 'tests/'],
  forbidden_scope: [],
  verification_commands: ['pytest -q tests/test_greet.py'],
  goal_criterion_ids: ['g-1'],
  required_capabilities: ['python'],
};

describe('changedContractFields', () => {
  it('sends nothing when the operator changed nothing', () => {
    expect(changedContractFields(loaded, { ...loaded })).toEqual({});
  });

  it('sends only the command when only the command changed', () => {
    const edited = { ...loaded, verification_commands: ['pytest -q tests/test_greeter.py'] };

    const fields = changedContractFields(loaded, edited);

    expect(fields).toEqual({ verification_commands: ['pytest -q tests/test_greeter.py'] });
    // The two that carry the destructive side effects must be absent.
    expect('acceptance_criteria' in fields).toBe(false);
    expect('objective' in fields).toBe(false);
    expect('required_capabilities' in fields).toBe(false);
  });

  it('sends the criteria when the criteria really changed', () => {
    const edited = {
      ...loaded,
      acceptance_criteria: [{ id: 't-1', description: 'greets by name' }],
    };

    expect(changedContractFields(loaded, edited)).toEqual({
      acceptance_criteria: [{ id: 't-1', description: 'greets by name' }],
    });
  });

  it('treats a reordered list as a change', () => {
    const edited = { ...loaded, allowed_scope: ['tests/', 'src/happy_path/'] };

    expect(changedContractFields(loaded, edited)).toEqual({
      allowed_scope: ['tests/', 'src/happy_path/'],
    });
  });

  it('sends capabilities only when they changed, because they force a rebind', () => {
    const edited = { ...loaded, required_capabilities: ['python', 'docker'] };

    expect(changedContractFields(loaded, edited)).toEqual({
      required_capabilities: ['python', 'docker'],
    });
  });

  it('sends several fields when several changed, in one revision', () => {
    const edited = {
      ...loaded,
      objective: 'implement a greeter',
      verification_strategy: 'characterization' as const,
    };

    expect(changedContractFields(loaded, edited)).toEqual({
      objective: 'implement a greeter',
      verification_strategy: 'characterization',
    });
  });
});

describe('contractFormBaseline', () => {
  it('normalizes the loaded contract the way the form does, so cosmetics are not a change', () => {
    /** The form parses text areas with trim-and-drop-empty. A stored value
     *  carrying stray whitespace would otherwise read as an edit the operator
     *  never made — and for `objective` that means a semantic edit, which
     *  discards the test bundle. */
    const baseline = contractFormBaseline(
      {
        objective: '  implement greet  ',
        acceptance_criteria: [{ id: ' t-1 ', description: ' greets ' }],
        verification_strategy: 'tdd',
        allowed_scope: ['src/happy_path/', '  '],
        forbidden_scope: null,
        verification_commands: [' pytest -q '],
        goal_criterion_ids: ['g-1'],
        required_capabilities: null,
      },
      ['python'],
    );

    expect(baseline).toEqual({
      objective: 'implement greet',
      acceptance_criteria: [{ id: 't-1', description: 'greets' }],
      verification_strategy: 'tdd',
      allowed_scope: ['src/happy_path/'],
      forbidden_scope: [],
      verification_commands: ['pytest -q'],
      goal_criterion_ids: ['g-1'],
      required_capabilities: ['python'],
    });
  });
});
