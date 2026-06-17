import React from 'react';
import { useNavigate } from 'react-router-dom';
import { Cpu, CircleDot } from 'lucide-react';
import { useAgents, useGoals } from '../lib/queries';
import { usePlannerStore } from '../store/plannerStore';
import { tokens } from '../styles/tokens';
import type { GoalAggregate, TaskSummary } from '../types/ui';

/** The task an agent is currently executing, if any (ASSIGNED or IN_PROGRESS). */
function currentTaskFor(agentId: string, goals: GoalAggregate[]): TaskSummary | null {
  for (const g of goals) {
    for (const t of g.tasks) {
      if (t.assigned_agent_id === agentId && (t.status === 'in_progress' || t.status === 'assigned')) {
        return t;
      }
    }
  }
  return null;
}

/**
 * Operational roster: every registered agent, whether it's alive, and the task
 * it's currently running — with a live tail of that task's output. Clicking the
 * task jumps to it on the canvas.
 */
export function AgentsView() {
  const { data: agents = [] } = useAgents();
  const { data: goals = [] } = useGoals();
  const taskProgress = usePlannerStore((s) => s.taskProgress);
  const selectNode = usePlannerStore((s) => s.selectNode);
  const navigate = useNavigate();

  const openTask = (taskId: string) => {
    selectNode(taskId);
    navigate('/');
  };

  return (
    <div style={{ padding: 18, overflowY: 'auto', height: '100%' }}>
      <h2 style={{
        fontSize: 13, fontFamily: tokens.fontMono, color: tokens.textPrimary,
        letterSpacing: '0.06em', marginBottom: 14, display: 'flex', alignItems: 'center', gap: 8,
      }}>
        <Cpu size={15} aria-hidden /> AGENTS
      </h2>

      {agents.length === 0 && (
        <p style={{ fontSize: 12, color: tokens.textMuted, fontFamily: tokens.fontMono }}>
          No agents registered. Use <code>orchestrate agents create</code>.
        </p>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {agents.map((a) => {
          const task = currentTaskFor(a.agent_id, goals);
          const running = task !== null;
          const dot = running ? tokens.yellow : a.alive ? tokens.green : tokens.textMuted;
          const stateLabel = running ? 'running' : a.alive ? 'idle' : 'offline';
          const lines = task ? taskProgress[task.task_id] : undefined;

          return (
            <div key={a.agent_id} style={{
              background: tokens.cardBg, border: `1px solid ${tokens.border}`,
              borderRadius: tokens.r12, padding: '12px 14px',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <CircleDot size={12} color={dot} aria-hidden />
                <span style={{ fontSize: 13, fontWeight: 600, color: tokens.textPrimary }}>{a.name}</span>
                <span style={{
                  fontSize: 9, fontFamily: tokens.fontMono, color: dot,
                  textTransform: 'uppercase', letterSpacing: '0.08em',
                }}>{stateLabel}</span>
                <span style={{ marginLeft: 'auto', fontSize: 9, fontFamily: tokens.fontMono, color: tokens.textMuted }}>
                  {a.agent_id}
                </span>
              </div>

              <div style={{ fontSize: 9, fontFamily: tokens.fontMono, color: tokens.textMuted, marginTop: 5 }}>
                {a.capabilities.join(' · ') || '—'} · v{a.version} · ≤{a.max_concurrent_tasks} concurrent
              </div>

              {task ? (
                <button
                  onClick={() => openTask(task.task_id)}
                  title="Open this task on the canvas"
                  style={{
                    marginTop: 8, width: '100%', textAlign: 'left', cursor: 'pointer',
                    background: tokens.yellow + '12', border: `1px solid ${tokens.yellow}33`,
                    borderRadius: 6, padding: '6px 8px',
                    fontSize: 10, fontFamily: tokens.fontMono, color: tokens.yellow,
                  }}
                >
                  ▸ {task.title || task.task_id}
                </button>
              ) : (
                <div style={{ marginTop: 8, fontSize: 10, fontFamily: tokens.fontMono, color: tokens.textMuted }}>
                  no task in flight
                </div>
              )}

              {lines && lines.length > 0 && (
                <div style={{
                  marginTop: 6, fontSize: 9, fontFamily: tokens.fontMono, color: tokens.textSecond,
                  background: '#0a0c12', border: `1px solid ${tokens.borderMuted}`,
                  borderRadius: 6, padding: '5px 7px', lineHeight: 1.4,
                  maxHeight: 110, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                }}>
                  {lines.slice(-6).join('\n')}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
