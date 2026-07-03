import React, { useEffect, useRef } from 'react';
import { Terminal, ChevronDown, ChevronUp } from 'lucide-react';
import { useParams } from 'react-router-dom';
import { tokens } from '../styles/tokens';
import { usePlannerStore } from '../store/plannerStore';

/**
 * Bottom console dock — the live agent feed. Tails the "agent.event" SSE
 * stream (fine-grained runtime telemetry: step/output events emitted by the
 * agent runners, deduped on event_id by the store), filtered to the plan the
 * operator is looking at.
 */
export function ConsoleDock() {
  const { planId = '' } = useParams();
  const consoleOpen = usePlannerStore((s) => s.ui.consoleOpen);
  const toggleConsole = usePlannerStore((s) => s.toggleConsole);
  const agentLog = usePlannerStore((s) => s.agentLog);

  const lines = planId ? agentLog.filter((l) => l.plan_id === planId) : agentLog;

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
      background: '#0d0f14',
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
                color: tokens.textSecond, whiteSpace: 'nowrap',
                overflow: 'hidden', textOverflow: 'ellipsis',
              }}>
                <span style={{ color: tokens.textDim }}>
                  {new Date(l.at).toLocaleTimeString()}{' '}
                </span>
                <span style={{ color: tokens.accent }}>{l.task_id.slice(0, 8)}</span>
                <span style={{ color: tokens.textDim }}> a{l.attempt}#{l.seq} </span>
                <span style={{ color: tokens.purple }}>{l.type}</span>{' '}
                {l.text}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
