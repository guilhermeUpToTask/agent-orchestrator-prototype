import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Terminal, ChevronDown, ChevronUp, ExternalLink } from 'lucide-react';
import { tokens, AGENT_COLORS } from '../styles/tokens';
import { usePlannerStore } from '../store/plannerStore';
import { useAgents, useGoals } from '../lib/queries';

/**
 * Bottom console dock — a global, always-reachable tail of live agent output.
 *
 * Live output also shows inside the per-task DetailPanel, but that's gated
 * behind selecting the exact running node. This dock surfaces whichever
 * task(s) are currently running independent of selection, so the operator can
 * watch the agent work without hunting for the right node.
 *
 * Data comes from the same `taskProgress` ring the DetailPanel reads, fed by
 * the `task.progress` SSE event (store.appendTaskProgress).
 */
export function ConsoleDock() {
  const consoleOpen = usePlannerStore((s) => s.ui.consoleOpen);
  const toggleConsole = usePlannerStore((s) => s.toggleConsole);
  const selectNode = usePlannerStore((s) => s.selectNode);
  const taskProgress = usePlannerStore((s) => s.taskProgress);

  const { data: goals = [] } = useGoals();
  const { data: agents = [] } = useAgents();

  // Every task currently executing, across all goals.
  const running = useMemo(() => {
    const out: { taskId: string; title: string; agentId: string | null }[] = [];
    for (const g of goals) {
      for (const t of g.tasks) {
        if (t.status === 'in_progress' || t.status === 'assigned') {
          out.push({ taskId: t.task_id, title: t.title, agentId: t.assigned_agent_id ?? null });
        }
      }
    }
    return out;
  }, [goals]);

  const [activeTab, setActiveTab] = useState<string | null>(null);
  // Keep the active tab pointing at a running task; default to the first and
  // follow newly-started runs once the previous one finishes.
  useEffect(() => {
    if (running.length === 0) {
      setActiveTab(null);
      return;
    }
    if (!activeTab || !running.some((r) => r.taskId === activeTab)) {
      setActiveTab(running[0].taskId);
    }
  }, [running, activeTab]);

  const lines = activeTab ? taskProgress[activeTab] : undefined;

  // Auto-scroll to the tail, but only while the operator is pinned to the
  // bottom — don't yank the view if they scrolled up to read history.
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);
  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
  };
  useEffect(() => {
    const el = scrollRef.current;
    if (el && pinnedRef.current) el.scrollTop = el.scrollHeight;
  }, [lines?.length, activeTab, consoleOpen]);

  const agentColorFor = (agentId: string | null): string => {
    if (!agentId) return tokens.textMuted;
    const a = agents.find((x) => x.agent_id === agentId);
    return a ? (AGENT_COLORS[a.name] ?? tokens.textSecond) : tokens.textMuted;
  };

  const runningCount = running.length;

  return (
    <div style={{
      flexShrink: 0,
      borderTop: `1px solid ${tokens.border}`,
      background: tokens.panelBg,
      display: 'flex', flexDirection: 'column',
    }}>
      {/* Header bar — always visible, toggles the dock */}
      <button
        onClick={toggleConsole}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '5px 12px', height: 28, flexShrink: 0,
          background: 'transparent', border: 'none', cursor: 'pointer',
          color: tokens.textSecond, fontFamily: tokens.fontMono, fontSize: 10,
          letterSpacing: '0.06em', textAlign: 'left', width: '100%',
        }}
      >
        <Terminal size={12} color={runningCount > 0 ? tokens.yellow : tokens.textMuted} />
        <span style={{ textTransform: 'uppercase', color: tokens.textMuted }}>Live console</span>
        {runningCount > 0 && (
          <span style={{
            display: 'inline-flex', alignItems: 'center', gap: 4,
            padding: '1px 7px', borderRadius: 8,
            background: tokens.yellow + '18', border: `1px solid ${tokens.yellow}33`,
            color: tokens.yellow, fontSize: 9,
          }}>
            <span style={{
              width: 5, height: 5, borderRadius: '50%', background: tokens.yellow,
              animation: 'pulse 1.2s ease-in-out infinite',
            }} />
            {runningCount} running
          </span>
        )}
        <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', color: tokens.textMuted }}>
          {consoleOpen ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
        </span>
      </button>

      {/* Body — tabs + terminal */}
      {consoleOpen && (
        <div style={{ display: 'flex', flexDirection: 'column', height: 220, borderTop: `1px solid ${tokens.borderMuted}` }}>
          {runningCount === 0 ? (
            <div style={{
              flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
              color: tokens.textMuted, fontFamily: tokens.fontMono, fontSize: 11,
            }}>
              No agent running — live output appears here.
            </div>
          ) : (
            <>
              {/* Tabs (one per running task) */}
              <div style={{
                display: 'flex', gap: 4, padding: '5px 8px', flexShrink: 0,
                overflowX: 'auto', borderBottom: `1px solid ${tokens.borderMuted}`,
              }}>
                {running.map((r) => {
                  const active = r.taskId === activeTab;
                  const color = agentColorFor(r.agentId);
                  return (
                    <button
                      key={r.taskId}
                      onClick={() => setActiveTab(r.taskId)}
                      title={r.title}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 5,
                        padding: '3px 9px', borderRadius: tokens.r4,
                        background: active ? '#141928' : 'transparent',
                        border: `1px solid ${active ? tokens.accent + '55' : tokens.borderMuted}`,
                        color: active ? tokens.textPrimary : tokens.textMuted,
                        cursor: 'pointer', fontFamily: tokens.fontMono, fontSize: 9,
                        whiteSpace: 'nowrap', flexShrink: 0,
                      }}
                    >
                      <span style={{ width: 6, height: 6, borderRadius: '50%', background: color, flexShrink: 0 }} />
                      {r.taskId}
                    </button>
                  );
                })}
                <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center' }}>
                  {activeTab && (
                    <button
                      onClick={() => selectNode(activeTab)}
                      title="Open this task's detail panel"
                      style={{
                        display: 'flex', alignItems: 'center', gap: 4,
                        padding: '3px 8px', borderRadius: tokens.r4,
                        background: 'transparent', border: `1px solid ${tokens.borderMuted}`,
                        color: tokens.textMuted, cursor: 'pointer',
                        fontFamily: tokens.fontMono, fontSize: 9, whiteSpace: 'nowrap',
                      }}
                    >
                      <ExternalLink size={10} /> open task
                    </button>
                  )}
                </div>
              </div>

              {/* Terminal body */}
              <div
                ref={scrollRef}
                onScroll={onScroll}
                style={{
                  flex: 1, overflow: 'auto', padding: '8px 12px',
                  fontFamily: tokens.fontMono, fontSize: 10, lineHeight: 1.5,
                  color: tokens.textSecond, background: '#0a0c12',
                  whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                }}
              >
                {lines && lines.length > 0 ? (
                  lines.join('\n')
                ) : (
                  <span style={{ color: tokens.textMuted }}>▍ waiting for agent output…</span>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
