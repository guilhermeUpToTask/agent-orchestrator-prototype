import React from 'react';
import { useParams } from 'react-router-dom';
import { Cpu, CircleDot } from 'lucide-react';
import { useAgents, usePlan } from '../lib/queries';
import { tokens } from '../styles/tokens';
import type { Goal, Task } from '../types/ui';

/** The task an agent is currently executing in this plan, if any. */
function currentTaskFor(agentId: string, goals: Goal[]): Task | null {
  for (const g of goals) {
    for (const t of g.tasks) {
      if (t.agent_id === agentId && t.status === 'running') return t;
    }
  }
  return null;
}

/**
 * The agent roster: every registered agent spec (id, role, capabilities)
 * and the task it is currently running in this plan.
 */
export function AgentsView() {
  const { planId = '' } = useParams();
  const { data: agents = [] } = useAgents();
  const { data: plan } = usePlan(planId || null);
  const goals = plan?.goals ?? [];

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
          No agents registered. Seed one with <code>orchestrate seed demo --stub</code>.
        </p>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {agents.map((a) => {
          const task = currentTaskFor(a.id, goals);
          const running = task !== null;
          const dot = running ? tokens.yellow : tokens.green;
          const capNames = (a.capabilities ?? []).map((c) => c.id ?? c.name);

          return (
            <div key={a.id} style={{
              background: tokens.cardBg, border: `1px solid ${tokens.border}`,
              borderRadius: tokens.r12, padding: '12px 14px',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <CircleDot size={12} color={dot} aria-hidden />
                <span style={{ fontSize: 13, fontWeight: 600, color: tokens.textPrimary }}>{a.name}</span>
                <span style={{
                  fontSize: 9, fontFamily: tokens.fontMono, color: dot,
                  textTransform: 'uppercase', letterSpacing: '0.08em',
                }}>{running ? 'running' : 'idle'}</span>
                <span style={{ marginLeft: 'auto', fontSize: 9, fontFamily: tokens.fontMono, color: tokens.textMuted }}>
                  {a.id}
                </span>
              </div>

              <div style={{ fontSize: 9, fontFamily: tokens.fontMono, color: tokens.textMuted, marginTop: 5 }}>
                {a.role} · {capNames.join(' · ') || 'no capabilities'}
              </div>

              {task ? (
                <div style={{
                  marginTop: 8,
                  background: tokens.yellow + '12', border: `1px solid ${tokens.yellow}33`,
                  borderRadius: 6, padding: '6px 8px',
                  fontSize: 10, fontFamily: tokens.fontMono, color: tokens.yellow,
                }}>
                  ▸ {task.name}
                </div>
              ) : (
                <div style={{ marginTop: 8, fontSize: 10, fontFamily: tokens.fontMono, color: tokens.textMuted }}>
                  no task in flight
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
