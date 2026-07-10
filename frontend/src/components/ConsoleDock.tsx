import React, { useEffect, useRef } from 'react';
import { Terminal, ChevronDown, ChevronUp } from 'lucide-react';
import { useParams } from 'react-router-dom';
import { tokens } from '../styles/tokens';
import { usePlannerStore } from '../store/plannerStore';

/** Color an agent-event line by severity (mirrors Activity's eventKind). */
function lineColor(type: string): string {
  if (type === 'agent.failed') return tokens.red;
  if (type === 'agent.finished') return tokens.green;
  if (type === 'llm.call') return tokens.purple;
  return tokens.textSecond;
}

/**
 * Bottom console dock — the live agent feed. Tails the "agent.event" SSE
 * stream (fine-grained runtime telemetry emitted by the agent runners and the
 * reasoner, deduped on event_id by the store), filtered to the plan the
 * operator is looking at. Failures are red, completions green, reasoner
 * llm.call rows purple; a toggle narrows to the selected task.
 */
export function ConsoleDock() {
  const { planId = '' } = useParams();
  const consoleOpen = usePlannerStore((s) => s.ui.consoleOpen);
  const toggleConsole = usePlannerStore((s) => s.toggleConsole);
  const agentLog = usePlannerStore((s) => s.agentLog);
  const selectedTaskId = usePlannerStore((s) => s.ui.selectedTaskId);
  const [taskOnly, setTaskOnly] = React.useState(false);

  const planLines = agentLog.filter((l) => !planId || l.plan_id === planId);
  // the task filter is only meaningful when the selected task has lines in the
  // plan being viewed — otherwise the toggle would silently blank the feed
  const selectedInPlan =
    !!selectedTaskId && planLines.some((l) => l.task_id === selectedTaskId);
  const lines = planLines.filter(
    (l) => !(taskOnly && selectedInPlan) || l.task_id === selectedTaskId,
  );

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
  }, [lines.length]);

  return (
    <div style={{
      borderTop: `1px solid ${tokens.border}`,
      background: 'var(--bg-0)',
      display: 'flex', flexDirection: 'column', flexShrink: 0,
      height: consoleOpen ? 200 : 30,
      transition: 'height 0.15s ease',
    }}>
      <button
        onClick={toggleConsole}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '6px 14px', background: 'transparent', border: 'none',
          cursor: 'pointer', color: tokens.textMuted, flexShrink: 0,
        }}
        aria-expanded={consoleOpen}
      >
        <Terminal size={12} aria-hidden />
        <span style={{ fontSize: 9, fontFamily: tokens.fontMono, letterSpacing: '0.1em' }}>
          AGENT CONSOLE {lines.length > 0 && `· ${lines.length}`}
        </span>
        <div style={{ flex: 1 }} />
        {selectedInPlan && (
          <span
            role="button"
            tabIndex={0}
            onClick={(e) => { e.stopPropagation(); setTaskOnly((v) => !v); }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') { e.stopPropagation(); setTaskOnly((v) => !v); }
            }}
            style={{
              fontSize: 9, fontFamily: tokens.fontMono, letterSpacing: '0.06em',
              padding: '2px 7px', borderRadius: 5, marginRight: 8,
              border: `1px solid ${tokens.border}`,
              color: taskOnly ? tokens.accent : tokens.textMuted,
              background: taskOnly ? 'color-mix(in srgb, var(--accent) 14%, transparent)' : 'transparent',
            }}
          >
            SELECTED TASK
          </span>
        )}
        {consoleOpen ? <ChevronDown size={13} aria-hidden /> : <ChevronUp size={13} aria-hidden />}
      </button>

      {consoleOpen && (
        <div
          ref={scrollRef}
          onScroll={onScroll}
          style={{ flex: 1, overflowY: 'auto', padding: '4px 14px 10px' }}
        >
          {lines.length === 0 ? (
            <span style={{ fontSize: 10, fontFamily: tokens.fontMono, color: tokens.textDim }}>
              No agent output yet — events stream here while tasks execute.
            </span>
          ) : (
            lines.map((l) => (
              <div key={l.id} style={{
                fontSize: 9.5, fontFamily: tokens.fontMono, lineHeight: 1.7,
                color: lineColor(l.type), whiteSpace: 'nowrap',
                overflow: 'hidden', textOverflow: 'ellipsis',
              }}>
                <span style={{ color: tokens.textDim }}>
                  {new Date(l.at).toLocaleTimeString()}{' '}
                </span>
                <span style={{ color: l.task_id ? tokens.accent : tokens.purple }}>
                  {l.task_id ? l.task_id.slice(0, 8) : 'plan'}
                </span>
                <span style={{ color: tokens.textDim }}> a{l.attempt}#{l.seq} </span>
                <span style={{ color: lineColor(l.type) }}>{l.type}</span>{' '}
                {l.text}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
