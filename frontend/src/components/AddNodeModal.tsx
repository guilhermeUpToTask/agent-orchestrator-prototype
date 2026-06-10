import React, { useState } from 'react';
import { X, Plus, Minus } from 'lucide-react';
import { tokens, AGENT_COLORS } from '../styles/tokens';
import { usePlannerStore } from '../store/plannerStore';
import type { GoalTaskDef } from '../types/domain';

const CAPABILITIES = [
  'code_generation', 'architecture', 'testing',
  'code_review', 'documentation', 'planning',
  'task_decomposition', 'refactoring', 'validation',
];

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontSize: 9, fontFamily: tokens.fontMono,
      color: tokens.textMuted, letterSpacing: '0.1em',
      marginBottom: 4,
    }}>
      {children}
    </div>
  );
}

function Input({ value, onChange, placeholder }: {
  value: string; onChange: (v: string) => void; placeholder?: string;
}) {
  return (
    <input
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      style={{
        width: '100%', background: tokens.inputBg,
        border: `1px solid ${tokens.border}`,
        borderRadius: tokens.r6, padding: '7px 10px',
        color: tokens.textPrimary, fontFamily: tokens.fontSans,
        fontSize: 12, outline: 'none', boxSizing: 'border-box',
        transition: 'border-color 0.15s',
      }}
      onFocus={(e) => (e.target.style.borderColor = tokens.accent)}
      onBlur={(e) => (e.target.style.borderColor = tokens.border)}
    />
  );
}

function Textarea({ value, onChange, placeholder, rows = 3 }: {
  value: string; onChange: (v: string) => void; placeholder?: string; rows?: number;
}) {
  return (
    <textarea
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      rows={rows}
      style={{
        width: '100%', background: tokens.inputBg,
        border: `1px solid ${tokens.border}`,
        borderRadius: tokens.r6, padding: '7px 10px',
        color: tokens.textPrimary, fontFamily: tokens.fontSans,
        fontSize: 12, outline: 'none', boxSizing: 'border-box',
        resize: 'vertical', lineHeight: 1.55,
        transition: 'border-color 0.15s',
      }}
      onFocus={(e) => (e.target.style.borderColor = tokens.accent)}
      onBlur={(e) => (e.target.style.borderColor = tokens.border)}
    />
  );
}

function Select<T extends string>({ value, options, onChange }: {
  value: T; options: { value: T; label: string }[]; onChange: (v: T) => void;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value as T)}
      style={{
        width: '100%', background: tokens.inputBg,
        border: `1px solid ${tokens.border}`,
        borderRadius: tokens.r6, padding: '7px 10px',
        color: tokens.textPrimary, fontFamily: tokens.fontSans,
        fontSize: 12, outline: 'none', boxSizing: 'border-box',
        cursor: 'pointer',
      }}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  );
}

function CriteriaEditor({ items, onChange }: {
  items: string[]; onChange: (items: string[]) => void;
}) {
  function update(i: number, val: string) {
    const next = [...items];
    next[i] = val;
    onChange(next);
  }
  function add() { onChange([...items, '']); }
  function remove(i: number) { onChange(items.filter((_, idx) => idx !== i)); }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {items.map((item, i) => (
        <div key={i} style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
          <input
            value={item}
            onChange={(e) => update(i, e.target.value)}
            placeholder={`Criterion ${i + 1}`}
            style={{
              flex: 1, background: tokens.inputBg,
              border: `1px solid ${tokens.border}`,
              borderRadius: tokens.r6, padding: '5px 8px',
              color: tokens.textPrimary, fontFamily: tokens.fontSans,
              fontSize: 11, outline: 'none',
            }}
            onFocus={(e) => (e.target.style.borderColor = tokens.accent)}
            onBlur={(e) => (e.target.style.borderColor = tokens.border)}
          />
          <button
            onClick={() => remove(i)}
            style={{
              background: 'transparent', border: `1px solid ${tokens.border}`,
              borderRadius: tokens.r4, width: 24, height: 24,
              color: tokens.textMuted, cursor: 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
          >
            <Minus size={10} />
          </button>
        </div>
      ))}
      <button
        onClick={add}
        style={{
          padding: '4px 8px', background: 'transparent',
          border: `1px dashed ${tokens.border}`, borderRadius: tokens.r6,
          color: tokens.textMuted, cursor: 'pointer',
          fontFamily: tokens.fontMono, fontSize: 9,
          display: 'flex', alignItems: 'center', gap: 4,
        }}
      >
        <Plus size={9} /> ADD CRITERION
      </button>
    </div>
  );
}

// ─── Main modal ───────────────────────────────────────────────────────────────

export function AddNodeModal() {
  const goals = usePlannerStore((s) => s.goals);
  const agentRegistry = usePlannerStore((s) => s.agentRegistry);
  const nodes = usePlannerStore((s) => s.nodes);
  const ui = usePlannerStore((s) => s.ui);
  const addTaskNode = usePlannerStore((s) => s.addTaskNode);
  const closeAddNodeModal = usePlannerStore((s) => s.closeAddNodeModal);

  const [goalId, setGoalId] = useState(goals[0]?.goal_id ?? '');
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [capability, setCapability] = useState<string>('code_generation');
  const [agentId, setAgentId] = useState(agentRegistry[2]?.agent_id ?? '');
  const [criteria, setCriteria] = useState<string[]>(['']);
  const [files, setFiles] = useState('');
  const [maxRetries, setMaxRetries] = useState('2');
  const [minVersion, setMinVersion] = useState('>=1.0.0');
  const [dependsOn, setDependsOn] = useState<string[]>([]);

  if (!ui.addNodeModalOpen) return null;

  const selectedAgent = agentRegistry.find((a) => a.agent_id === agentId);
  const agentColor = selectedAgent ? (AGENT_COLORS[selectedAgent.name] ?? tokens.textSecond) : tokens.textMuted;

  function handleSubmit() {
    if (!title.trim()) return;
    const taskDef: Omit<GoalTaskDef, 'task_id'> = {
      title: title.trim(),
      description: description.trim() || 'Task description pending.',
      depends_on: dependsOn,
      capability,
      files_allowed_to_modify: files.split('\n').map((f) => f.trim()).filter(Boolean),
      acceptance_criteria: criteria.filter((c) => c.trim()),
      max_retries: parseInt(maxRetries, 10) || 2,
      min_version: minVersion,
      constraints: agentId ? { preferred_agent: agentId } : {},
    };
    addTaskNode(goalId, taskDef);
    closeAddNodeModal();
  }

  const availableTaskIds = nodes.map((n) => n.id);

  return (
    <div style={{
      position: 'fixed', inset: 0,
      background: 'rgba(0,0,0,0.75)',
      zIndex: 100,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      animation: 'fadein 0.15s ease both',
    }}>
      <div style={{
        width: 480, maxHeight: '88vh',
        background: tokens.panelBg,
        border: `1px solid ${tokens.borderFocus}`,
        borderRadius: tokens.r12,
        overflow: 'hidden',
        boxShadow: `0 0 60px ${tokens.accentGlow}, 0 24px 64px rgba(0,0,0,0.8)`,
        display: 'flex', flexDirection: 'column',
      }}>
        {/* Header */}
        <div style={{
          padding: '14px 18px',
          borderBottom: `1px solid ${tokens.border}`,
          display: 'flex', alignItems: 'center', gap: 8,
          background: '#0d0f16', flexShrink: 0,
        }}>
          <div style={{
            width: 7, height: 7, borderRadius: '50%',
            background: tokens.green, boxShadow: `0 0 6px ${tokens.green}`,
          }} />
          <span style={{ fontFamily: tokens.fontMono, fontSize: 11, color: tokens.textPrimary, letterSpacing: '0.08em' }}>
            ADD TASK NODE
          </span>
          <div style={{ flex: 1 }} />
          <button onClick={closeAddNodeModal} style={{
            background: 'transparent', border: 'none',
            color: tokens.textMuted, cursor: 'pointer', padding: 4,
            display: 'flex', alignItems: 'center',
          }}>
            <X size={14} />
          </button>
        </div>

        {/* Scrollable form */}
        <div style={{ flex: 1, overflowY: 'auto', padding: 18, display: 'flex', flexDirection: 'column', gap: 14 }}>

          {/* Goal */}
          <div>
            <Label>TARGET GOAL</Label>
            <Select
              value={goalId}
              options={goals.map((g) => ({ value: g.goal_id, label: `${g.name} — ${g.description.slice(0, 50)}` }))}
              onChange={setGoalId}
            />
          </div>

          {/* Title */}
          <div>
            <Label>TITLE *</Label>
            <Input value={title} onChange={setTitle} placeholder="e.g. Implement retry policy" />
          </div>

          {/* Description */}
          <div>
            <Label>DESCRIPTION</Label>
            <Textarea value={description} onChange={setDescription} placeholder="What should this task accomplish? Include technical details…" rows={3} />
          </div>

          {/* Two-col: capability + agent */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <Label>CAPABILITY</Label>
              <Select
                value={capability as string}
                options={CAPABILITIES.map((c) => ({ value: c, label: c }))}
                onChange={(v) => setCapability(v)}
              />
            </div>
            <div>
              <Label>PREFERRED AGENT</Label>
              <select
                value={agentId}
                onChange={(e) => setAgentId(e.target.value)}
                style={{
                  width: '100%', background: tokens.inputBg,
                  border: `1px solid ${agentColor + '44'}`,
                  borderRadius: tokens.r6, padding: '7px 10px',
                  color: agentColor, fontFamily: tokens.fontSans,
                  fontSize: 12, outline: 'none', cursor: 'pointer',
                }}
              >
                <option value="">-- unassigned --</option>
                {agentRegistry.map((a) => (
                  <option key={a.agent_id} value={a.agent_id}>
                    {a.name} v{a.version}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Acceptance criteria */}
          <div>
            <Label>ACCEPTANCE CRITERIA</Label>
            <CriteriaEditor items={criteria} onChange={setCriteria} />
          </div>

          {/* Dependencies */}
          <div>
            <Label>DEPENDS ON</Label>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
              {availableTaskIds.map((tid) => {
                const selected = dependsOn.includes(tid);
                return (
                  <button
                    key={tid}
                    onClick={() => setDependsOn((prev) =>
                      selected ? prev.filter((x) => x !== tid) : [...prev, tid],
                    )}
                    style={{
                      padding: '2px 8px', borderRadius: tokens.r4,
                      background: selected ? tokens.accentDim : 'transparent',
                      border: `1px solid ${selected ? tokens.accent : tokens.border}`,
                      color: selected ? tokens.accent : tokens.textMuted,
                      cursor: 'pointer', fontSize: 9, fontFamily: tokens.fontMono,
                      transition: 'all 0.15s',
                    }}
                  >
                    {tid}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Files */}
          <div>
            <Label>FILES ALLOWED TO MODIFY (one per line)</Label>
            <Textarea value={files} onChange={setFiles} placeholder="src/app/usecases/my_usecase.py" rows={2} />
          </div>

          {/* Two-col: retries + version */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <Label>MAX RETRIES</Label>
              <Input value={maxRetries} onChange={setMaxRetries} placeholder="2" />
            </div>
            <div>
              <Label>MIN VERSION</Label>
              <Input value={minVersion} onChange={setMinVersion} placeholder=">=1.0.0" />
            </div>
          </div>
        </div>

        {/* Footer */}
        <div style={{
          padding: '12px 18px',
          borderTop: `1px solid ${tokens.border}`,
          display: 'flex', gap: 10,
          flexShrink: 0,
        }}>
          <button
            onClick={handleSubmit}
            disabled={!title.trim()}
            style={{
              flex: 1, padding: '10px',
              background: title.trim() ? tokens.accent : tokens.accentDim,
              border: 'none', borderRadius: tokens.r8,
              color: title.trim() ? '#fff' : tokens.textMuted,
              cursor: title.trim() ? 'pointer' : 'default',
              fontFamily: tokens.fontMono, fontSize: 11,
              letterSpacing: '0.06em',
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
              transition: 'background 0.15s',
            }}
          >
            <Plus size={12} /> ADD TO PLAN
          </button>
          <button
            onClick={closeAddNodeModal}
            style={{
              padding: '10px 18px', background: 'transparent',
              border: `1px solid ${tokens.border}`, borderRadius: tokens.r8,
              color: tokens.textMuted, cursor: 'pointer',
              fontFamily: tokens.fontMono, fontSize: 11,
            }}
          >
            CANCEL
          </button>
        </div>
      </div>
    </div>
  );
}
