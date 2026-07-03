import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Send, ChevronRight, Bot, User, Loader2 } from 'lucide-react';
import { useParams } from 'react-router-dom';
import { tokens } from '../styles/tokens';
import { usePlannerStore } from '../store/plannerStore';
import { useChat, usePlan, useSendMessage } from '../lib/queries';
import type { ChatMessageResponse } from '../types/ui';

/**
 * The conversation surface for the two chat-driven phases. History is SERVER
 * state (GET /plans/{id}/chat — survives reloads); sending posts one turn and
 * refetches. A reply with committed=true is the roadmap commit — the plan
 * advances and the input locks until the next conversational phase.
 */
function Bubble({ msg }: { msg: ChatMessageResponse }) {
  const isUser = msg.role === 'user';
  const committed = msg.meta?.committed === true;
  const time = new Date(msg.created_at).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
  });

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
          {isUser ? 'you' : 'reasoner'} · {time}
        </span>
        {committed && (
          <span style={{
            fontSize: 8, fontFamily: tokens.fontMono, color: tokens.green,
            background: tokens.greenDim, padding: '1px 6px', borderRadius: 3,
          }}>roadmap committed</span>
        )}
      </div>
      <div style={{
        maxWidth: '88%', padding: '8px 12px',
        background: isUser ? tokens.accentDim : tokens.cardBg,
        border: `1px solid ${isUser ? tokens.accent + '33' : tokens.border}`,
        borderRadius: isUser ? '10px 10px 2px 10px' : '10px 10px 10px 2px',
        fontSize: 12, color: tokens.textPrimary, fontFamily: tokens.fontSans,
        lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
      }}>{msg.content}</div>
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
      <span style={{ fontSize: 11, color: tokens.textMuted, fontFamily: tokens.fontMono }}>reasoning…</span>
    </div>
  );
}

const MODE_HINTS: Record<string, string> = {
  discovery:
    'Describe what you want built. The reasoner may ask questions; when the direction is clear it commits the goal roadmap.',
  replanning:
    'Plan the next iteration. Completed goals are history; describe what should happen next.',
};

export function ChatPanel() {
  const { planId = '' } = useParams();
  const ui = usePlannerStore((s) => s.ui);
  const toggleChatPanel = usePlannerStore((s) => s.toggleChatPanel);

  const { data: plan } = usePlan(planId || null);
  const { data: history = [] } = useChat(planId || null);
  const sendMessage = useSendMessage(planId);

  const [input, setInput] = useState('');
  const endRef = useRef<HTMLDivElement>(null);

  const thinking = sendMessage.isPending;

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [history.length, thinking]);

  const phase = plan?.phase ?? 'discovery';
  const inputEnabled = phase === 'discovery' || phase === 'replanning';

  const send = useCallback(
    (text: string) => {
      if (!text.trim() || thinking || !inputEnabled) return;
      setInput('');
      sendMessage.mutate(text);
    },
    [thinking, inputEnabled, sendMessage],
  );

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send(input);
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
          CHAT · REASONER
        </div>
      </div>
    );
  }

  const hint = inputEnabled
    ? MODE_HINTS[phase]
    : `The plan is in “${phase}” — chat re-opens in DISCOVERY or REPLANNING.`;
  const inputLocked = thinking || !inputEnabled;

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
          width: 7, height: 7, borderRadius: '50%',
          background: inputEnabled ? tokens.accent : tokens.textMuted,
          boxShadow: inputEnabled ? `0 0 8px ${tokens.accent}` : undefined,
        }} />
        <span style={{ fontFamily: tokens.fontMono, fontSize: 11, color: tokens.textPrimary, letterSpacing: '0.08em' }}>
          CHAT · REASONER
        </span>
        <span style={{
          fontSize: 8, fontFamily: tokens.fontMono, padding: '2px 6px', borderRadius: 3,
          background: inputEnabled ? tokens.purpleDim : '#1c2030',
          color: inputEnabled ? tokens.purple : tokens.textMuted,
          marginLeft: 4,
        }}>
          {phase.toUpperCase()}
        </span>
        <div style={{ flex: 1 }} />
        <button onClick={toggleChatPanel} style={{ background: 'transparent', border: 'none', color: tokens.textMuted, cursor: 'pointer', padding: 2, display: 'flex', alignItems: 'center' }}>
          <ChevronRight size={14} />
        </button>
      </div>

      {/* Mode hint banner */}
      <div style={{
        padding: '6px 14px', borderBottom: `1px solid ${tokens.border}`,
        background: inputEnabled ? 'transparent' : '#16121f',
        fontSize: 9, fontFamily: tokens.fontMono, lineHeight: 1.5,
        color: inputEnabled ? tokens.textMuted : tokens.purple, flexShrink: 0,
      }}>
        {hint}
      </div>

      {/* Messages (server history) */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 10 }}>
        {history.length === 0 && !thinking && (
          <span style={{ fontSize: 10, fontFamily: tokens.fontMono, color: tokens.textMuted, lineHeight: 1.6 }}>
            {inputEnabled
              ? 'No messages yet — describe the work to start planning.'
              : 'No conversation for this phase.'}
          </span>
        )}
        {history.map((m, i) => <Bubble key={i} msg={m} />)}
        {thinking && <ThinkingBubble />}
        <div ref={endRef} />
      </div>

      {/* Input */}
      <div style={{ padding: '10px 12px', borderTop: `1px solid ${tokens.border}`, display: 'flex', gap: 8, alignItems: 'flex-end', flexShrink: 0 }}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={inputEnabled ? 'Message the reasoner… (Enter to send)' : 'Chat is closed in this phase'}
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
