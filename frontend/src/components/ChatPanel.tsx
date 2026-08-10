import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Send, ChevronRight, Bot, User, Loader2 } from 'lucide-react';
import { useParams } from 'react-router-dom';
import styles from './ChatPanel.module.css';
import { usePlannerStore } from '../store/plannerStore';
import { useChat, usePlan, useSendMessage } from '../lib/queries';
import type { ChatMessageResponse } from '../types/ui';
import { conversationMode } from '../lib/planTruth';

/** Join the class names that are actually present. */
const cx = (...names: (string | false | undefined)[]) => names.filter(Boolean).join(' ');

/**
 * The conversation surface for the two chat-driven phases. History is SERVER
 * state (GET /plans/{id}/chat — survives reloads); sending posts one turn and
 * refetches. A reply with committed=true is the roadmap commit — the plan
 * advances and the input locks until the next conversational phase.
 */
function Bubble({ msg }: { msg: ChatMessageResponse }) {
  const isUser = msg.role === 'user';
  const committed = msg.meta?.committed === true;
  const submittedBrief = msg.meta?.submitted_brief === true;
  const time = new Date(msg.created_at).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
  });

  return (
    <div className={cx(styles.bubble, isUser && styles.bubbleUser)}>
      <div className={styles.bubbleMeta}>
        <div className={cx(styles.avatar, isUser && styles.avatarUser)}>
          {isUser ? <User size={10} /> : <Bot size={10} />}
        </div>
        <span className={styles.byline}>
          {isUser ? 'you' : 'reasoner'} · {time}
        </span>
        {committed && <span className={styles.committedChip}>intent ready for review</span>}
        {submittedBrief && isUser && <span className={styles.briefChip}>submitted brief</span>}
      </div>
      <div className={cx(styles.body, isUser && styles.bodyUser)}>{msg.content}</div>
    </div>
  );
}

function ThinkingBubble({ label }: { label: string }) {
  return (
    <div className={styles.thinking}>
      <Loader2 size={12} className={styles.thinkingIcon} />
      <span className={styles.thinkingLabel}>{label}</span>
    </div>
  );
}

const MODE_HINTS: Record<string, string> = {
  discovery:
    'Describe what you want built. The reasoner may ask questions; when the direction is clear it commits the goal roadmap.',
  replanning:
    'Plan the next cycle. Completed goals are history; describe what should happen next.',
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

  const mode = conversationMode(plan);
  const inputEnabled = mode !== null && !plan?.pending_gate;

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
      <div className={styles.rail} onClick={toggleChatPanel}>
        <ChevronRight size={14} />
        <div className={styles.railLabel}>CHAT · REASONER</div>
      </div>
    );
  }

  const hint = inputEnabled
    ? MODE_HINTS[mode!]
    : `Current activity is “${humanize(plan?.activity ?? 'loading')}” — chat opens during intent or replan discovery.`;
  const inputLocked = thinking || !inputEnabled;

  const sendDisabled = inputLocked || !input.trim();

  return (
    <div className={styles.panel}>
      {/* Header */}
      <div className={styles.header}>
        <div className={cx(styles.statusDot, inputEnabled && styles.statusDotLive)} />
        <span className={styles.headerTitle}>CHAT · REASONER</span>
        <span className={cx(styles.modeChip, inputEnabled && styles.modeChipLive)}>
          {(mode ?? plan?.activity ?? 'loading').toUpperCase()}
        </span>
        <div className={styles.spacer} />
        <button
          onClick={toggleChatPanel}
          className={styles.collapseButton}
          aria-label="Collapse the chat panel"
        >
          <ChevronRight size={14} />
        </button>
      </div>

      {/* Mode hint banner */}
      <div className={cx(styles.hint, inputEnabled && styles.hintLive)}>{hint}</div>

      {/* Messages (server history) */}
      <div className={styles.messages}>
        {history.length === 0 && !thinking && (
          <span className={styles.empty}>
            {inputEnabled
              ? 'No messages yet — describe the work to start planning.'
              : 'No conversation for this phase.'}
          </span>
        )}
        {history.map((m, i) => <Bubble key={i} msg={m} />)}
        {thinking && <ThinkingBubble label={plan?.planning_progress ?? 'Analyzing brief…'} />}
        <div ref={endRef} />
      </div>

      {/* Input */}
      <div className={styles.composer}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={inputEnabled ? 'Message the reasoner… (Enter to send)' : 'Chat is closed in this phase'}
          rows={2}
          disabled={inputLocked}
          className={styles.input}
        />
        <button
          onClick={() => send(input)}
          disabled={sendDisabled}
          className={styles.send}
          aria-label="Send message to the reasoner"
        >
          <Send size={14} />
        </button>
      </div>
    </div>
  );
}

function humanize(value: string): string {
  return value.replace(/_/g, ' ');
}
