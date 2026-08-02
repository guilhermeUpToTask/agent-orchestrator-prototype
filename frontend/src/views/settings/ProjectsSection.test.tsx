import React from 'react';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ProjectsSection } from './ProjectsSection';

/**
 * The wizard's load-bearing property is a negative one, so it needs a test that
 * drives it: DECLINING THE DELIVERY TOKEN MUST NOT CHANGE THE REPOSITORY. That
 * is the substitution the phase forbids, and nothing about the rendered markup
 * would reveal it — only pressing the buttons does.
 */
const mocks = vi.hoisted(() => ({
  createProject: vi.fn((_body: unknown, opts?: { onSuccess?: (p: unknown) => void }) =>
    opts?.onSuccess?.({ id: 'p1' }),
  ),
  probe: vi.fn(
    (_url: string, opts?: { onSuccess?: (p: unknown) => void }) =>
      opts?.onSuccess?.({
        binding: 'remote',
        reachable: false,
        default_branch: null,
        resolved_path_preview: '/home/u/.orchestrator/projects/x/repos/ab12',
        problem: 'fatal: could not read Username for https://…',
        problem_kind: 'needs_credentials',
      }),
  ),
  clone: vi.fn(),
  setForge: vi.fn(),
}));

vi.mock('../../lib/queries', () => ({
  useProjects: () => ({ data: [] }),
  useCreateProject: () => ({ mutate: mocks.createProject, isPending: false }),
  useUpdateProject: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteProject: () => ({ mutate: vi.fn(), isPending: false }),
  useProbeRepository: () => ({ mutate: mocks.probe, isPending: false }),
  useCloneProject: () => ({ mutate: mocks.clone, isPending: false }),
  useSetForgeBinding: () => ({ mutate: mocks.setForge, isPending: false }),
}));

beforeEach(() => {
  for (const fn of Object.values(mocks)) (fn as ReturnType<typeof vi.fn>).mockClear?.();
});
afterEach(cleanup);

async function openWizard() {
  render(<ProjectsSection />);
  await userEvent.click(screen.getByRole('button', { name: /add project/i }));
}

describe('the repository-choice wizard', () => {
  it('never substitutes a scratch repository when the pull-request option is declined', async () => {
    await openWizard();

    await userEvent.type(screen.getByLabelText(/^name$/i), 'storefront');
    await userEvent.click(screen.getByRole('radio', { name: /clone a remote/i }));
    await userEvent.type(
      screen.getByLabelText(/repository url/i),
      'https://github.com/acme/widgets.git',
    );
    await userEvent.click(screen.getByRole('button', { name: /^continue$/i }));

    // Decline delivery: this must change only how work comes back.
    await userEvent.click(screen.getByRole('radio', { name: /leave it on a branch/i }));
    await userEvent.click(screen.getByRole('button', { name: /^continue$/i }));
    await userEvent.click(screen.getByRole('button', { name: /create project/i }));

    expect(mocks.createProject).toHaveBeenCalledWith(
      {
        name: 'storefront',
        repo_url: 'https://github.com/acme/widgets.git',
        binding: 'remote',
      },
      expect.anything(),
    );
    expect(mocks.setForge).not.toHaveBeenCalled();
  });

  it('binds the forge only when the operator asks for a pull request', async () => {
    await openWizard();

    await userEvent.type(screen.getByLabelText(/^name$/i), 'storefront');
    await userEvent.click(screen.getByRole('radio', { name: /point at a local repository/i }));
    await userEvent.type(screen.getByLabelText(/repository url/i), '/home/u/code/app');
    await userEvent.click(screen.getByRole('button', { name: /^continue$/i }));

    await userEvent.click(screen.getByRole('radio', { name: /open a pull request for me/i }));
    await userEvent.type(screen.getByLabelText(/github repository/i), 'acme/widgets');
    await userEvent.type(screen.getByLabelText(/^token$/i), 'ghp_secret');
    await userEvent.click(screen.getByRole('button', { name: /^continue$/i }));
    await userEvent.click(screen.getByRole('button', { name: /create project/i }));

    expect(mocks.setForge).toHaveBeenCalledWith({
      id: 'p1',
      body: { repository: 'acme/widgets', token: 'ghp_secret' },
    });
  });

  it('names the binding it was told, rather than inferring it from a blank field', async () => {
    await openWizard();

    await userEvent.type(screen.getByLabelText(/^name$/i), 'demo');
    await userEvent.click(screen.getByRole('radio', { name: /create an empty one/i }));
    await userEvent.click(screen.getByRole('button', { name: /^continue$/i }));
    await userEvent.click(screen.getByRole('button', { name: /^continue$/i }));
    await userEvent.click(screen.getByRole('button', { name: /create project/i }));

    expect(mocks.createProject).toHaveBeenCalledWith(
      { name: 'demo', repo_url: null, binding: 'scratch' },
      expect.anything(),
    );
  });

  it('reports a probe failure by kind rather than showing git stderr', async () => {
    await openWizard();

    await userEvent.click(screen.getByRole('radio', { name: /clone a remote/i }));
    await userEvent.type(screen.getByLabelText(/repository url/i), 'https://x/y.git');
    await userEvent.click(screen.getByRole('button', { name: /check/i }));

    expect(await screen.findByText(/needs credentials/i)).toBeTruthy();
    expect(screen.queryByText(/could not read Username/i)).toBeNull();
  });
});
