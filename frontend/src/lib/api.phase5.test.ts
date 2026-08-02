import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  applyEdit,
  bindProject,
  createModel,
  deletePlan,
  subscribeToAttemptLog,
  updateModel,
  updateProvider,
} from './api';

function response(status = 204, body?: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
  });
}

afterEach(() => vi.unstubAllGlobals());

/**
 * Requests are SAME-ORIGIN by default. These assertions used to hardcode
 * `http://localhost:8000`, which was the old default base — and that default
 * was the packaging defect: the UI shipped inside the wheel called port 8000
 * no matter which port `orchestrate serve` was given. What matters here is the
 * PATH each call builds, so that is what these assert.
 */

describe('Phase 5 critical frontend API contracts', () => {
  it('sends the complete task contract repair body', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response());
    vi.stubGlobal('fetch', fetchMock);

    await applyEdit('plan/1', {
      type: 'update_task_contract',
      goal_id: 'goal-1',
      task_id: 'task-1',
      objective: 'Ship safely',
      acceptance_criteria: [{ id: 'criterion-1', description: 'It works' }],
      verification_strategy: 'tdd',
      allowed_scope: ['frontend/src'],
      forbidden_scope: ['backend'],
      verification_commands: ['npm test'],
      goal_criterion_ids: ['goal-criterion-1'],
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/plans/plan%2F1/edits');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toMatchObject({
      type: 'update_task_contract',
      task_id: 'task-1',
      verification_strategy: 'tdd',
      forbidden_scope: ['backend'],
      verification_commands: ['npm test'],
    });
  });

  it('preserves provider and model capacity overrides', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response())
      .mockResolvedValueOnce(response(200, { id: 'model-1' }))
      .mockResolvedValueOnce(response());
    vi.stubGlobal('fetch', fetchMock);

    await updateProvider('provider-1', {
      name: 'Primary',
      base_url: 'https://provider.example',
      max_inflight: 7,
      capacity_scope: 'endpoint_wide',
    });
    await createModel('provider-1', 'reasoner', 3);
    await updateModel('model-1', 'reasoner-v2', null);

    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({
      max_inflight: 7,
      capacity_scope: 'endpoint_wide',
    });
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      name: 'reasoner',
      max_inflight: 3,
    });
    expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toEqual({
      name: 'reasoner-v2',
      max_inflight: null,
    });
  });

  it('uses stable delete and project-binding endpoints', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response());
    vi.stubGlobal('fetch', fetchMock);

    await bindProject('plan-1', 'project-1');
    await deletePlan('plan-1');

    expect(fetchMock.mock.calls[0][0]).toBe(
      '/api/plans/plan-1/project-binding',
    );
    expect(fetchMock.mock.calls[0][1].method).toBe('POST');
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ project_id: 'project-1' });
    expect(fetchMock.mock.calls[1][0]).toBe('/api/plans/plan-1');
    expect(fetchMock.mock.calls[1][1].method).toBe('DELETE');
  });

  it('parses raw attempt SSE lines, truncation, offsets, and end', async () => {
    const frames = [
      'event: truncated\ndata: {}\n\n',
      'id: 42\ndata: {"monotonic_seconds":1.25,"stream":"stdout","text":"tests passed\\n"}\n\n',
      'event: end\ndata: {}\n\n',
    ].join('');
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(frames, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const entries: unknown[] = [];
    const truncated = vi.fn();

    await new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('attempt stream did not end')), 500);
      subscribeToAttemptLog('plan-1', 'attempt-1', {
        onEntry: (entry) => entries.push(entry),
        onTruncated: truncated,
        onEnd: () => {
          clearTimeout(timeout);
          resolve();
        },
      });
    });

    expect(fetchMock.mock.calls[0][0]).toBe(
      '/api/plans/plan-1/attempts/attempt-1/log/stream?offset=0',
    );
    expect(truncated).toHaveBeenCalledOnce();
    expect(entries).toEqual([
      { monotonic_seconds: 1.25, stream: 'stdout', text: 'tests passed\n' },
    ]);
  });
});
