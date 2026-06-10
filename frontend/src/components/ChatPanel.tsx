import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Send, ChevronRight, Bot, User, Loader2, Settings2 } from 'lucide-react';
import { tokens } from '../styles/tokens';
import { usePlannerStore } from '../store/plannerStore';
import { startDiscovery } from '../lib/api';
import type { ChatMessage } from '../types/domain';

function Bubble({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === 'user';
  const isSystem = msg.role === 'system';

  if (isSystem) {
    return (
      <div style={{
        padding: '4px 10px', background: '#0f1117',
        border: `1px solid ${tokens.border}`, borderRadius: tokens.r6,
        fontSize: 9, fontFamily: tokens.fontMono, color: tokens.textMuted,
        letterSpacing: '0.04em', lineHeight: 1.6,
      }}>
        ⚙ {msg.text}
        <span style={{ marginLeft: 8, color: tokens.textDim }}>{msg.ts}</span>
      </div>
    );
  }

  return (
    <div style={{
      display: 'flex', flexDirection: 'column',
      alignItems: isUser ? 'flex-end' : 'flex-start',
      gap: 3, animation: 'fadein 0.15s ease both',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexDirection: isUser ? 'row-reverse' : 'row' }}>
        <div style={{
          width: 20, height: 20, borderRadius: '50%',
          background: isUser ? tokens.accentDim : '#1a1d2a',
          border: `1px solid ${isUser ? tokens.accent + '44' : tokens.border}`,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          {isUser ? <User size={10} color={tokens.accent} /> : <Bot size={10} color={tokens.purple} />}
        </div>
        <span style={{ fontSize: 8, fontFamily: tokens.fontMono, color: tokens.textMuted }}>
          {isUser ? 'you' : 'aipom'} · {msg.ts}
        </span>
        {msg.nodeCtx && (
          <span style={{
            fontSize: 8, fontFamily: tokens.fontMono, color: tokens.accent,
            background: tokens.accentGlow, padding: '1px 6px', borderRadius: 3,
          }}>{msg.nodeCtx}</span>
        )}
      </div>
      <div style={{
        maxWidth: '88%', padding: '8px 12px',
        background: isUser ? tokens.accentDim : tokens.cardBg,
        border: `1px solid ${isUser ? tokens.accent + '33' : tokens.border}`,
        borderRadius: isUser ? '10px 10px 2px 10px' : '10px 10px 10px 2px',
        fontSize: 12, color: tokens.textPrimary, fontFamily: tokens.fontSans,
        lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
      }}>{msg.text}</div>
    </div>
  );
}

function ThinkingBubble() {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8,
      padding: '8px 12px', background: tokens.cardBg,
      border: `1px solid ${tokens.border}`,
      borderRadius: '10px 10px 10px 2px',
      width: 'fit-content', animation: 'fadein 0.15s ease both',
    }}>
      <Loader2 size={12} color={tokens.purple} style={{ animation: 'spin 1s linear infinite' }} />
      <span style={{ fontSize: 11, color: tokens.textMuted, fontFamily: tokens.fontMono }}>thinking…</span>
    </div>
  );
}

// Context-aware placeholder text
function placeholderFor(planStatus: string, nodeId: string | null): string {
  if (nodeId) return `Feedback on ${nodeId}… (Enter to send)`;
  switch (planStatus) {
    case 'phase_active': return 'Reassign a task, add a step, fix acceptance criteria…';
    case 'discovery': return 'Answer the planner\'s question…';
    default: return 'Ask about the plan…';
  }
}

// Context-aware quick actions
function quickActionsFor(planStatus: string): string[] {
  switch (planStatus) {
    case 'phase_active':
      return [
        'What tasks are blocking progress?',
        'Reassign task-X to reviewer agent',
        'Add a missing test for goal-Y',
        'Why is the last task stuck?',
      ];
    case 'discovery':
      return [
        'The project is a CLI tool for file management',
        'Primary constraint is Python 3.10+',
        'MVP ships in 4 weeks',
      ];
    default:
      return ['What is the current plan status?', 'Explain the architecture summary'];
  }
}

export function ChatPanel() {
  const messages = usePlannerStore((s) => s.messages);
  const ui = usePlannerStore((s) => s.ui);
  const plan = usePlannerStore((s) => s.plan);
  const nodes = usePlannerStore((s) => s.nodes);
  const sendMessage = usePlannerStore((s) => s.sendMessage);
  const addMessage = usePlannerStore((s) => s.addMessage);
  const setThinking = usePlannerStore((s) => s.setThinking);
  const toggleChatPanel = usePlannerStore((s) => s.toggleChatPanel);

  const [input, setInput] = useState('');
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages, ui.isThinking]);

  const planStatus = plan?.status ?? 'discovery';
  const selectedNode = nodes.find((n) => n.id === ui.selectedNodeId);

  const send = useCallback(async (text: string) => {
    if (!text.trim() || ui.isThinking) return;
    setInput('');
    await sendMessage(text);
  }, [ui.isThinking, sendMessage]);

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input); }
  }

  async function handleStartDiscovery() {
    setThinking(true);
    try {
      const result = await startDiscovery();
      const now = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
      if (result.question) {
        addMessage({ role: 'assistant', text: result.question, ts: now });
      } else if (result.done) {
        addMessage({ role: 'assistant', text: 'Discovery complete. Brief ready for approval.', ts: now });
      }
    } catch (err) {
      addMessage({ role: 'system', text: `Start discovery failed: ${err}`, ts: new Date().toLocaleTimeString() });
    } finally {
      setThinking(false);
    }
  }

  if (ui.chatPanelCollapsed) {
    return (
      <div style={{
        width: 36, background: tokens.panelBg, borderLeft: `1px solid ${tokens.border}`,
        display: 'flex', flexDirection: 'column', alignItems: 'center', paddingTop: 12, cursor: 'pointer',
      }} onClick={toggleChatPanel}>
        <ChevronRight size={14} color={tokens.textMuted} />
        <div style={{ writingMode: 'vertical-rl', marginTop: 12, fontSize: 9, fontFamily: tokens.fontMono, color: tokens.textMuted, letterSpacing: '0.1em' }}>
          CHAT · AIPOM
        </div>
      </div>
    );
  }

  const quickActions = quickActionsFor(planStatus);

  return (
    <div style={{
      width: 320, flexShrink: 0, display: 'flex', flexDirection: 'column',
      background: tokens.panelBg, borderLeft: `1px solid ${tokens.border}`,
    }}>
      {/* Header */}
      <div style={{
        padding: '10px 14px', borderBottom: `1px solid ${tokens.border}`,
        display: 'flex', alignItems: 'center', gap: 8, background: '#0d0f16', flexShrink: 0,
      }}>
        <div style={{
          width: 7, height: 7, borderRadius: '50%', background: tokens.accent,
          boxShadow: `0 0 8px ${tokens.accent}`,
          animation: 'glow 2.5s ease-in-out infinite',
          ['--glow-color' as string]: tokens.accent,
        }} />
        <span style={{ fontFamily: tokens.fontMono, fontSize: 11, color: tokens.textPrimary, letterSpacing: '0.08em' }}>
          CHAT · AIPOM
        </span>
        {/* Plan status chip */}
        <span style={{
          fontSize: 8, fontFamily: tokens.fontMono, padding: '2px 6px',
          borderRadius: 3, background: '#1c2030', color: tokens.textMuted,
          marginLeft: 4,
        }}>
          {planStatus === 'phase_active' ? '⚡ LIVE' : planStatus.toUpperCase()}
        </span>
        <div style={{ flex: 1 }} />
        <button onClick={toggleChatPanel} style={{ background: 'transparent', border: 'none', color: tokens.textMuted, cursor: 'pointer', padding: 2, display: 'flex', alignItems: 'center' }}>
          <ChevronRight size={14} />
        </button>
      </div>

      {/* Context strip */}
      {selectedNode && (
        <div style={{
          padding: '6px 14px', background: tokens.accentGlow,
          borderBottom: `1px solid ${tokens.accentDim}`,
          display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0,
        }}>
          <Settings2 size={9} color={tokens.accent} />
          <span style={{ fontSize: 9, fontFamily: tokens.fontMono, color: tokens.accent }}>
            ctx: {selectedNode.id} · {selectedNode.data.task?.status}
          </span>
        </div>
      )}

      {/* Discovery start button */}
      {planStatus === 'discovery' && messages.length <= 2 && (
        <div style={{ padding: '10px 14px', borderBottom: `1px solid ${tokens.border}`, flexShrink: 0 }}>
          <button onClick={handleStartDiscovery} disabled={ui.isThinking} style={{
            width: '100%', padding: '8px', background: tokens.purpleDim,
            border: `1px solid ${tokens.purple}44`, borderRadius: tokens.r6,
            color: tokens.purple, cursor: 'pointer', fontFamily: tokens.fontMono,
            fontSize: 10, letterSpacing: '0.06em',
          }}>
            ▶ START DISCOVERY SESSION
          </button>
        </div>
      )}

      {/* Messages */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 10 }}>
        {messages.map((m) => <Bubble key={m.id} msg={m} />)}
        {ui.isThinking && <ThinkingBubble />}
        <div ref={endRef} />
      </div>

      {/* Quick actions */}
      {messages.length <= 3 && (
        <div style={{ padding: '0 14px 10px', display: 'flex', flexDirection: 'column', gap: 4, flexShrink: 0 }}>
          <div style={{ fontSize: 8, fontFamily: tokens.fontMono, color: tokens.textMuted, marginBottom: 2, letterSpacing: '0.08em' }}>
            QUICK ACTIONS
          </div>
          {quickActions.map((q) => (
            <button key={q} onClick={() => send(q)} style={{
              padding: '5px 8px', background: tokens.cardBg,
              border: `1px solid ${tokens.border}`, borderRadius: tokens.r6,
              color: tokens.textSecond, cursor: 'pointer',
              fontFamily: tokens.fontSans, fontSize: 10, textAlign: 'left',
            }}>
              {q}
            </button>
          ))}
        </div>
      )}

      {/* Input */}
      <div style={{ padding: '10px 12px', borderTop: `1px solid ${tokens.border}`, display: 'flex', gap: 8, alignItems: 'flex-end', flexShrink: 0 }}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={placeholderFor(planStatus, ui.selectedNodeId)}
          rows={2}
          disabled={ui.isThinking}
          style={{
            flex: 1, background: tokens.inputBg,
            border: `1px solid ${tokens.border}`, borderRadius: tokens.r8,
            padding: '8px 10px', fontFamily: tokens.fontSans, fontSize: 12,
            color: tokens.textPrimary, outline: 'none', resize: 'none', lineHeight: 1.5,
          }}
          onFocus={(e) => (e.target.style.borderColor = tokens.accent)}
          onBlur={(e) => (e.target.style.borderColor = tokens.border)}
        />
        <button
          onClick={() => send(input)}
          disabled={ui.isThinking || !input.trim()}
          style={{
            width: 36, height: 36, borderRadius: tokens.r8,
            background: ui.isThinking || !input.trim() ? '#1a1d2a' : tokens.accent,
            border: 'none', cursor: ui.isThinking || !input.trim() ? 'default' : 'pointer',
            display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
          }}
        >
          <Send size={14} color={ui.isThinking || !input.trim() ? tokens.textMuted : '#fff'} />
        </button>
      </div>
    </div>
  );
}
