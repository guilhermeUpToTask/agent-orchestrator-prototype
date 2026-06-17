import React from 'react';
import { X, GitPullRequest, ExternalLink, CheckCircle2, XCircle, CircleDashed } from 'lucide-react';
import { tokens, STATUS_META, AGENT_COLORS, GOAL_STATUS_META, type StatusKey } from '../styles/tokens';
import { usePlannerStore } from '../store/plannerStore';
import { useAgents, useGoals, useSendChatMessage, useTaskLogs } from '../lib/queries';
import type { GoalAggregate } from '../types/ui';

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontSize: 9, fontFamily: tokens.fontMono, color: tokens.textMuted,
      letterSpacing: '0.1em', marginBottom: 4, textTransform: 'uppercase',
    }}>{children}</div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <Label>{label}</Label>
      {children}
    </div>
  );
}

function Tag({ text, color }: { text: string; color?: string }) {
  const c = color ?? tokens.accent;
  return (
    <span style={{
      padding: '2px 8px', borderRadius: tokens.r4,
      background: c + '18', border: `1px solid ${c + '33'}`,
      fontSize: 9, fontFamily: tokens.fontMono, color: c,
    }}>{text}</span>
  );
}

export function DetailPanel() {
  const selectedNodeId = usePlannerStore((s) => s.ui.selectedNodeId);
  const detailPanelOpen = usePlannerStore((s) => s.ui.detailPanelOpen);
  const selectNode = usePlannerStore((s) => s.selectNode);
  const progress = usePlannerStore((s) => (selectedNodeId ? s.taskProgress[selectedNodeId] : undefined));

  const { data: goals = [] } = useGoals();
  const { data: agentRegistry = [] } = useAgents();
  const sendMessage = useSendChatMessage();

  const goal = goals.find((g) => g.tasks.some((t) => t.task_id === selectedNodeId));
  const task = goal?.tasks.find((t) => t.task_id === selectedNodeId);

  // Persisted console logs — fetched once the task is in a terminal state.
  const isTerminalTask = !!task && ['succeeded', 'merged', 'failed', 'canceled'].includes(task.status);
  const { data: taskLogs } = useTaskLogs(selectedNodeId, detailPanelOpen && isTerminalTask);

  if (!detailPanelOpen || !task) return null;

  const agent = task.assigned_agent_id
    ? agentRegistry.find((a) => a.agent_id === task.assigned_agent_id) ?? null
    : null;
  const status = (task.status ?? 'created') as StatusKey;
  const meta = STATUS_META[status] ?? STATUS_META.created;
  const agentColor = agent ? (AGENT_COLORS[agent.name] ?? tokens.textSecond) : tokens.textMuted;

  // Find blocking deps from goal task list
  const goalTasks = goal?.tasks ?? [];
  const blockingDeps = goalTasks
    .filter((t) => t.status !== 'succeeded' && t.status !== 'merged')
    .map((t) => t.task_id);

  // Capture in arrow consts so TS narrowing from the guard above carries
  // into the click handlers.
  const askExplain = () => {
    sendMessage(`explain_task ${task.task_id} — why is it ${task.status}?`);
  };

  const askReassign = (agentName: string) => {
    sendMessage(`Reassign ${task.task_id} to ${agentName}`);
  };

  return (
    <div className="anim-slidein" style={{
      position: 'absolute', top: 0, right: 0,
      width: 300, height: '100%',
      background: tokens.panelBg,
      borderLeft: `1px solid ${tokens.border}`,
      display: 'flex', flexDirection: 'column',
      zIndex: 20,
      boxShadow: '-8px 0 32px rgba(0,0,0,0.5)',
    }}>
      {/* Header */}
      <div style={{
        padding: '12px 14px', borderBottom: `1px solid ${tokens.border}`,
        display: 'flex', alignItems: 'center', gap: 8,
        background: '#0d0f16', flexShrink: 0,
      }}>
        <div style={{ width: 7, height: 7, borderRadius: '50%', background: meta.dot, boxShadow: `0 0 6px ${meta.dot}` }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 10, fontFamily: tokens.fontMono, color: tokens.textMuted, letterSpacing: '0.08em' }}>TASK</div>
          <div style={{ fontSize: 11, fontFamily: tokens.fontMono, color: tokens.textSecond, marginTop: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {task?.task_id}
          </div>
        </div>
        <button onClick={() => selectNode(null)} style={{
          background: 'transparent', border: 'none', color: tokens.textMuted,
          cursor: 'pointer', padding: 4, display: 'flex', alignItems: 'center',
        }}>
          <X size={14} />
        </button>
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflowY: 'auto', padding: 14 }}>

        {/* Badges row */}
        <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginBottom: 14 }}>
          <Tag text={meta.label} color={meta.color} />
          {agent && <Tag text={agent.name} color={agentColor} />}
          {goal && <Tag text={goal.name} color={tokens.textMuted} />}
        </div>

        <Field label="Title">
          <span style={{ fontSize: 14, fontWeight: 600, color: tokens.textPrimary, lineHeight: 1.35 }}>
            {task.title || task.task_id}
          </span>
        </Field>

        <Field label="Status">
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <div style={{ width: 6, height: 6, borderRadius: '50%', background: meta.dot }} />
            <span style={{ fontSize: 12, color: meta.color, fontFamily: tokens.fontMono }}>{meta.label}</span>
            {(task.retry_count ?? 0) > 0 && (
              <span style={{ fontSize: 10, color: tokens.yellow, fontFamily: tokens.fontMono }}>
                (retry {task.retry_count})
              </span>
            )}
          </div>
        </Field>

        {progress && progress.length > 0 && (
          <Field label="Live output">
            <div style={{
              fontSize: 10, fontFamily: tokens.fontMono, color: tokens.textSecond,
              background: '#0a0c12', border: `1px solid ${tokens.borderMuted}`,
              borderRadius: 6, padding: '6px 8px', lineHeight: 1.45,
              maxHeight: 200, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            }}>
              {progress.slice(-100).join('\n')}
            </div>
          </Field>
        )}

        {task.unassignable_reason && status !== 'succeeded' && status !== 'merged' && (
          <Field label="Cannot assign">
            <div style={{
              fontSize: 11, color: tokens.yellow, fontFamily: tokens.fontMono,
              lineHeight: 1.4, background: tokens.yellow + '14',
              border: `1px solid ${tokens.yellow}33`, borderRadius: 6, padding: '6px 8px',
            }}>
              ⚠ {task.unassignable_reason}
            </div>
          </Field>
        )}

        {task.last_error && status === 'failed' && (
          <Field label="Failure reason">
            <div style={{
              fontSize: 11, color: tokens.red, fontFamily: tokens.fontMono,
              lineHeight: 1.4, background: tokens.redDim,
              border: `1px solid ${tokens.red}33`, borderRadius: 6, padding: '6px 8px',
              whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 160, overflow: 'auto',
            }}>
              {task.last_error}
            </div>
          </Field>
        )}

        {(status === 'succeeded' || status === 'merged') && (task.commit_sha || (task.modified_files?.length ?? 0) > 0) && (
          <Field label="Outcome">
            <div style={{
              fontSize: 11, color: tokens.green, fontFamily: tokens.fontMono,
              background: tokens.greenDim, border: `1px solid ${tokens.green}33`,
              borderRadius: 6, padding: '6px 8px', lineHeight: 1.5,
            }}>
              {task.commit_sha && <div>✓ commit {task.commit_sha.slice(0, 10)}</div>}
              {(task.modified_files?.length ?? 0) > 0 && (
                <div style={{ color: tokens.textSecond, marginTop: 3 }}>
                  {task.modified_files!.length} file(s): {task.modified_files!.slice(0, 6).join(', ')}
                </div>
              )}
            </div>
          </Field>
        )}

        {/* Persisted console log (after completion) */}
        {isTerminalTask && taskLogs && (taskLogs.stdout || taskLogs.stderr) && (
          <Field label="Console log">
            <div style={{
              fontSize: 10, fontFamily: tokens.fontMono, color: tokens.textSecond,
              background: '#0a0c12', border: `1px solid ${tokens.borderMuted}`,
              borderRadius: 6, padding: '6px 8px', lineHeight: 1.45,
              maxHeight: 260, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            }}>
              {taskLogs.stdout}
              {taskLogs.stderr && `\n--- stderr ---\n${taskLogs.stderr}`}
            </div>
          </Field>
        )}

        <Field label="Agent">
          {agent ? (
            <div>
              <div style={{ fontSize: 12, color: agentColor, fontWeight: 500 }}>{agent.name}</div>
              <div style={{ fontSize: 10, color: tokens.textMuted, marginTop: 2, fontFamily: tokens.fontMono }}>
                {agent.capabilities.join(' · ')}
              </div>
              <div style={{ fontSize: 9, color: tokens.textMuted, marginTop: 1, fontFamily: tokens.fontMono }}>
                v{agent.version} · trust: {agent.trust_level}
              </div>
            </div>
          ) : (
            <span style={{ fontSize: 11, color: tokens.textMuted, fontFamily: tokens.fontMono }}>unassigned</span>
          )}
        </Field>

        {/* Goal context */}
        {goal && (
          <Field label="Goal">
            <div style={{ fontSize: 11, color: tokens.textSecond }}>
              {goal.name}
              <span style={{
                fontSize: 9, marginLeft: 6, fontFamily: tokens.fontMono,
                color: GOAL_STATUS_META[goal.status]?.color ?? tokens.textMuted,
              }}>
                [{GOAL_STATUS_META[goal.status]?.label ?? goal.status}]
              </span>
            </div>
            {goal.depends_on.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 5 }}>
                {goal.depends_on.map((d) => <Tag key={d} text={`↳ ${d}`} color={tokens.purple} />)}
              </div>
            )}
          </Field>
        )}

        {/* GitHub PR review gate */}
        {goal && <PRGate goal={goal} />}

        {/* Blocking deps */}
        {(status === 'created' || status === 'assigned') && blockingDeps.length > 0 && (
          <Field label="Waiting on">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              {blockingDeps.slice(0, 5).map((dep) => (
                <div key={dep} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                  <div style={{ width: 5, height: 5, borderRadius: '50%', background: tokens.yellow, flexShrink: 0 }} />
                  <span style={{ fontSize: 9, fontFamily: tokens.fontMono, color: tokens.yellow }}>{dep}</span>
                </div>
              ))}
            </div>
          </Field>
        )}

        {/* Retry info */}
        <Field label="Retry policy">
          <div style={{ display: 'flex', gap: 14 }}>
            <div>
              <div style={{ fontSize: 9, color: tokens.textMuted, fontFamily: tokens.fontMono }}>RETRIES USED</div>
              <div style={{ fontSize: 14, fontWeight: 700, color: (task.retry_count ?? 0) > 0 ? tokens.yellow : tokens.textPrimary }}>
                {task.retry_count ?? 0}
              </div>
            </div>
          </div>
        </Field>

        {/* AIPOM quick-chat actions */}
        <Field label="Quick Actions (via chat)">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
            <button onClick={askExplain} style={quickBtnStyle(tokens.accent)}>
              🔍 Explain why this task is in its current state
            </button>
            {agentRegistry.filter((a) => a.agent_id !== agent?.agent_id).slice(0, 3).map((a) => (
              <button key={a.agent_id} onClick={() => askReassign(a.name)} style={quickBtnStyle(AGENT_COLORS[a.name] ?? tokens.textMuted)}>
                ⇄ Reassign to {a.name}
              </button>
            ))}
          </div>
        </Field>
      </div>
    </div>
  );
}

/**
 * GitHub PR review gate panel for the goal that owns the selected task.
 * Goals awaiting PR approval get a purple highlighted border; the operator
 * can jump straight to the PR on GitHub. The orchestrator never merges PRs —
 * this gate is where the human takes over.
 */
function PRGate({ goal }: { goal: GoalAggregate }) {
  const awaiting = goal.status === 'awaiting_pr_approval';
  const hasPR = goal.pr_number != null;

  if (!hasPR && !awaiting) return null;

  const gateColor = awaiting ? tokens.purple : goal.pr_status === 'merged' ? tokens.green : tokens.accent;

  function Check({ ok, label }: { ok: boolean | null | undefined; label: string }) {
    const Icon = ok ? CheckCircle2 : ok === false ? XCircle : CircleDashed;
    const color = ok ? tokens.green : ok === false ? tokens.red : tokens.textMuted;
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <Icon size={11} color={color} />
        <span style={{ fontSize: 10, fontFamily: tokens.fontMono, color }}>{label}</span>
      </div>
    );
  }

  return (
    <Field label="PR Review Gate">
      <div style={{
        padding: '10px 12px',
        background: awaiting ? `${tokens.purple}10` : tokens.cardBg,
        border: `1px solid ${gateColor}55`,
        borderRadius: tokens.r6,
        display: 'flex', flexDirection: 'column', gap: 7,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <GitPullRequest size={12} color={gateColor} />
          <span style={{ fontSize: 11, fontFamily: tokens.fontMono, color: gateColor }}>
            {hasPR ? `PR #${goal.pr_number}` : 'PR pending'}
          </span>
          <span style={{ fontSize: 9, fontFamily: tokens.fontMono, color: tokens.textMuted }}>
            {goal.pr_status ?? 'not opened'}
          </span>
        </div>

        {hasPR && (
          <>
            <Check ok={goal.pr_checks_passed} label={goal.pr_checks_passed ? 'CI checks passed' : 'CI checks pending/failing'} />
            <Check ok={goal.pr_approved} label={goal.pr_approved ? 'Review approved' : 'Awaiting review approval'} />
          </>
        )}

        {goal.pr_html_url && (
          <a
            href={goal.pr_html_url}
            target="_blank"
            rel="noreferrer"
            style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
              padding: '6px 10px', borderRadius: tokens.r6,
              background: gateColor + '18', border: `1px solid ${gateColor}44`,
              color: gateColor, fontSize: 10, fontFamily: tokens.fontMono,
              textDecoration: 'none', letterSpacing: '0.04em',
            }}
          >
            <ExternalLink size={11} /> OPEN PR ON GITHUB
          </a>
        )}

        {awaiting && (
          <div style={{ fontSize: 9, fontFamily: tokens.fontMono, color: tokens.textMuted, lineHeight: 1.5 }}>
            Merging happens on GitHub — the orchestrator advances once the PR is merged.
          </div>
        )}
      </div>
    </Field>
  );
}

function quickBtnStyle(color: string): React.CSSProperties {
  return {
    padding: '6px 10px', background: color + '10',
    border: `1px solid ${color + '30'}`, borderRadius: tokens.r6,
    color, cursor: 'pointer', fontFamily: tokens.fontSans, fontSize: 10,
    textAlign: 'left', lineHeight: 1.4,
  };
}
