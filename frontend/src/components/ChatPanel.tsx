import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Send, ChevronRight, Bot, User, Loader2, Settings2, Wrench } from 'lucide-react';
import { tokens } from '../styles/tokens';
import { usePlannerStore } from '../store/plannerStore';
import { useGoals, usePlan, useSendChatMessage, useStartDiscovery } from '../lib/queries';
import type { ChatMessage, ChatMode } from '../types/ui';

function ToolCallBubble({ msg }: { msg: ChatMessage }) {
  return (
    <div style={{
      padding: '6px 10px', background: '#131022',
      border: `1px solid ${tokens.purple}33`, borderLeft: `2px solid ${tokens.purple}`,
      borderRadius: tokens.r6, animation: 'fadein 0.15s ease both',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginBottom: 3 }}>
        <Wrench size={9} color={tokens.purple} />
        <span style={{ fontSize: 8, fontFamily: tokens.fontMono, color: tokens.purple, letterSpacing: '0.06em' }}>
          tool · {msg.toolName ?? 'planner'}
        </span>
        <span style={{ fontSize: 8, fontFamily: tokens.fontMono, color: tokens.textDim }}>{msg.ts}</span>
      </div>
      <div style={{
        fontSize: 11, color: tokens.textSecond, fontFamily: tokens.fontMono,
        lineHeight: 1.5, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
      }}>{msg.text}</div>
    </div>
  );
}

function Bubble({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === 'user';
  const isSystem = msg.role === 'system';

  if (msg.role === 'tool') return <ToolCallBubble msg={msg} />;

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

// Derive the chat mode from the plan status — mirrors the backend prompt
// builders (discovery / architecture / phase_review / tactical refinement).
function chatModeFor(planStatus: string): ChatMode {
  switch (planStatus) {
    case 'discovery':
      return {
        key: 'discovery',
        label: 'DISCOVERY Q&A',
        inputEnabled: true,
        hint: 'The planner asks questions; your answers build the project brief. Approve the brief in the toolbar when it is ready.',
      };
    case 'architecture':
      return {
        key: 'awaiting-architecture',
        label: 'AWAITING ARCHITECTURE APPROVAL',
        inputEnabled: false,
        hint: 'The planner drafted architecture decisions. Review the proposed decisions above and use “Approve Architecture” in the toolbar.',
      };
    case 'phase_active':
      return {
        key: 'tactical',
        label: 'TACTICAL REFINEMENT',
        inputEnabled: true,
        hint: 'Chat is wired to the tactical planner — request task changes and it mutates the live plan.',
      };
    case 'phase_review':
      return {
        key: 'awaiting-phase-review',
        label: 'AWAITING PHASE REVIEW',
        inputEnabled: false,
        hint: 'The phase review is in progress. Use “Approve Phase” in the toolbar to release the next phase or finish the project.',
      };
    case 'done':
      return {
        key: 'done',
        label: 'PROJECT DONE',
        inputEnabled: false,
        hint: 'All phases are complete and merged. Chat is read-only.',
      };
    default:
      return {
        key: 'tactical',
        label: planStatus.toUpperCase(),
        inputEnabled: true,
        hint: 'Ask about the plan.',
      };
  }
}

// Context-aware placeholder text
function placeholderFor(mode: ChatMode, nodeId: string | null): string {
  if (!mode.inputEnabled) {
    switch (mode.key) {
      case 'awaiting-architecture': return 'Waiting for architecture approval — use the toolbar';
      case 'awaiting-phase-review': return 'Waiting for phase review approval — use the toolbar';
      default: return 'Project complete';
    }
  }
  if (nodeId) return `Feedback on ${nodeId}… (Enter to send)`;
  switch (mode.key) {
    case 'tactical': return 'Reassign a task, add a step, fix acceptance criteria…';
    case 'discovery': return 'Answer the planner\'s question…';
    default: return 'Ask about the plan…';
  }
}

// Context-aware quick actions
function quickActionsFor(mode: ChatMode): string[] {
  switch (mode.key) {
    case 'tactical':
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
      return [];
  }
}

export function ChatPanel() {
  const messages = usePlannerStore((s) => s.messages);
  const ui = usePlannerStore((s) => s.ui);
  const toggleChatPanel = usePlannerStore((s) => s.toggleChatPanel);

  const { data: plan } = usePlan();
  const { data: goals = [] } = useGoals();
  const sendMessage = useSendChatMessage();
  const startDiscovery = useStartDiscovery();

  const [input, setInput] = useState('');
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages, ui.isThinking]);

  const planStatus = plan?.status ?? 'discovery';
  const mode = chatModeFor(planStatus);
  const selectedTask = ui.selectedNodeId
    ? goals.flatMap((g) => g.tasks).find((t) => t.task_id === ui.selectedNodeId) ?? null
    : null;

  const send = useCallback(async (text: string) => {
    if (!text.trim() || ui.isThinking || !mode.inputEnabled) return;
    setInput('');
    await sendMessage(text);
  }, [ui.isThinking, sendMessage, mode.inputEnabled]);

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input); }
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

  const quickActions = quickActionsFor(mode);
  const inputLocked = ui.isThinking || !mode.inputEnabled;

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
        {/* Chat mode chip */}
        <span style={{
          fontSize: 8, fontFamily: tokens.fontMono, padding: '2px 6px',
          borderRadius: 3,
          background: mode.key === 'tactical' ? tokens.accentGlow : mode.key === 'discovery' ? tokens.purpleDim : '#1c2030',
          color: mode.key === 'tactical' ? tokens.accent : mode.key === 'discovery' ? tokens.purple : tokens.textMuted,
          marginLeft: 4,
        }}>
          {mode.key === 'tactical' ? '⚡ ' : mode.key === 'discovery' ? '? ' : ''}{mode.label}
        </span>
        <div style={{ flex: 1 }} />
        <button onClick={toggleChatPanel} style={{ background: 'transparent', border: 'none', color: tokens.textMuted, cursor: 'pointer', padding: 2, display: 'flex', alignItems: 'center' }}>
          <ChevronRight size={14} />
        </button>
      </div>

      {/* Mode hint banner */}
      <div style={{
        padding: '6px 14px', borderBottom: `1px solid ${tokens.border}`,
        background: mode.inputEnabled ? 'transparent' : '#16121f',
        fontSize: 9, fontFamily: tokens.fontMono, lineHeight: 1.5,
        color: mode.inputEnabled ? tokens.textMuted : tokens.purple, flexShrink: 0,
      }}>
        {mode.hint}
      </div>

      {/* Context strip */}
      {selectedTask && (
        <div style={{
          padding: '6px 14px', background: tokens.accentGlow,
          borderBottom: `1px solid ${tokens.accentDim}`,
          display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0,
        }}>
          <Settings2 size={9} color={tokens.accent} />
          <span style={{ fontSize: 9, fontFamily: tokens.fontMono, color: tokens.accent }}>
            ctx: {selectedTask.task_id} · {selectedTask.status}
          </span>
        </div>
      )}

      {/* Discovery start button */}
      {planStatus === 'discovery' && messages.length <= 2 && (
        <div style={{ padding: '10px 14px', borderBottom: `1px solid ${tokens.border}`, flexShrink: 0 }}>
          <button onClick={() => startDiscovery.mutate()} disabled={ui.isThinking} style={{
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
      {quickActions.length > 0 && messages.length <= 3 && (
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
          placeholder={placeholderFor(mode, ui.selectedNodeId)}
          rows={2}
          disabled={inputLocked}
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
          disabled={inputLocked || !input.trim()}
          style={{
            width: 36, height: 36, borderRadius: tokens.r8,
            background: inputLocked || !input.trim() ? '#1a1d2a' : tokens.accent,
            border: 'none', cursor: inputLocked || !input.trim() ? 'default' : 'pointer',
            display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
          }}
        >
          <Send size={14} color={inputLocked || !input.trim() ? tokens.textMuted : '#fff'} />
        </button>
      </div>
    </div>
  );
}
