import React, { memo } from 'react';
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react';
import type { TaskNodeData } from '../types/ui';
import { tokens, STATUS, raw } from '../styles/tokens';
import { usePlannerStore } from '../store/plannerStore';

const KIND_COLOR = {
  idle: raw.idle, run: raw.run, gate: raw.gate, ok: raw.ok, fail: raw.fail,
} as const;

function PulsingDot({ color }: { color: string }) {
  return (
    <div style={{
      width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
      background: color, boxShadow: `0 0 6px ${color}`,
      animation: 'glow 2.5s ease-in-out infinite',
      ['--glow-color' as string]: color,
    }} />
  );
}

function TaskNodeComponent({ id, data }: NodeProps<Node<TaskNodeData>>) {
  const selectTask = usePlannerStore((s) => s.selectTask);
  const selectedTaskId = usePlannerStore((s) => s.ui.selectedTaskId);
  const isSelected = selectedTaskId === id;

  const task = data.task;
  const status = task?.status ?? 'pending';
  const meta = STATUS[status] ?? STATUS.pending;
  const color = KIND_COLOR[meta.kind];
  const isRunning = status === 'running';
  const isDone = status === 'done';
  const isFailed = status === 'failed';
  const isSkipped = status === 'skipped';

  return (
    <>
      <Handle type="target" position={Position.Left} style={{
        background: tokens.accentDim, border: `1.5px solid ${isSelected ? tokens.accent : '#2a3050'}`,
        width: 8, height: 8, left: -4,
      }} />

      <div onClick={() => selectTask(isSelected ? null : id)} style={{
        width: 240,
        background: isSelected ? '#141928' : tokens.cardBg,
        border: `1.5px solid ${isSelected ? tokens.accent : isRunning ? tokens.yellow + '44' : isFailed ? tokens.red + '33' : tokens.border}`,
        borderRadius: tokens.r12, cursor: 'pointer', userSelect: 'none',
        boxShadow: isSelected
          ? `0 0 0 1px ${tokens.accentGlow}, 0 8px 32px rgba(0,0,0,0.7)`
          : isRunning ? `0 0 16px ${tokens.yellow}22, 0 4px 12px rgba(0,0,0,0.5)`
          : '0 4px 16px rgba(0,0,0,0.4)',
        transition: 'box-shadow 0.2s, border-color 0.2s',
        animation: 'fadein 0.18s ease both', overflow: 'hidden',
        opacity: isSkipped ? 0.55 : 1,
      }}>
        {/* Status stripe */}
        <div style={{ height: 3, background: `linear-gradient(90deg, ${color}, transparent)` }} />

        {/* Header */}
        <div style={{
          padding: '7px 10px 5px', borderBottom: `1px solid ${tokens.borderMuted}`,
          display: 'flex', alignItems: 'center', gap: 6,
        }}>
          <PulsingDot color={color} />
          <span style={{ fontSize: 9, fontFamily: tokens.fontMono, color, letterSpacing: '0.1em', fontWeight: 600 }}>
            {meta.label.toUpperCase()}
          </span>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
            <span style={{
              fontSize: 8, fontFamily: tokens.fontMono, padding: '1px 5px',
              borderRadius: 3, background: '#1c2030', color: tokens.textMuted,
            }}>{data.goalName}</span>
            {data.agent && (
              <span style={{
                fontSize: 8, fontFamily: tokens.fontMono, padding: '1px 5px',
                borderRadius: 3, background: tokens.accent + '18',
                border: `1px solid ${tokens.accent}33`, color: tokens.accent,
              }}>{data.agent.name}</span>
            )}
          </div>
        </div>

        {/* Body */}
        <div style={{ padding: '8px 10px 10px' }}>
          <div style={{
            fontSize: 12, fontWeight: 600, color: tokens.textPrimary,
            lineHeight: 1.35, marginBottom: 6,
          }}>
            {task?.name ?? id}
          </div>

          {(task?.attempt ?? 0) > 1 && (
            <div style={{
              fontSize: 9, fontFamily: tokens.fontMono,
              color: tokens.yellow, marginBottom: 4,
            }}>
              ↺ attempt {task.attempt}
            </div>
          )}

          {task?.required_capabilities?.length > 0 && (
            <div style={{
              fontSize: 8, fontFamily: tokens.fontMono, color: tokens.textMuted,
              marginBottom: 4,
            }}>
              caps: {task.required_capabilities.join(', ')}
            </div>
          )}

          {isRunning && (
            <div style={{ display: 'flex', gap: 4, alignItems: 'center', marginBottom: 4 }}>
              {[0, 1, 2].map((i) => (
                <div key={i} style={{
                  width: 4, height: 4, borderRadius: '50%', background: tokens.yellow,
                  animation: `pulse 1.2s ${i * 0.18}s ease-in-out infinite`,
                }} />
              ))}
              <span style={{ fontSize: 9, color: tokens.yellow, fontFamily: tokens.fontMono }}>executing…</span>
            </div>
          )}

          {isDone && (
            <div style={{
              padding: '3px 7px', borderRadius: 5,
              background: tokens.greenDim, border: `1px solid ${tokens.green}33`,
              fontSize: 9, fontFamily: tokens.fontMono, color: tokens.green,
            }}>
              ✓ done
            </div>
          )}

          {isFailed && (
            <div style={{
              padding: '3px 7px', borderRadius: 5,
              background: tokens.redDim, border: `1px solid ${tokens.red}33`,
              fontSize: 9, fontFamily: tokens.fontMono, color: tokens.red,
              lineHeight: 1.3,
              display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
            }}>
              ✗ {task?.result?.failure_reason ?? 'failed'}
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{
          padding: '3px 10px', borderTop: `1px solid ${tokens.borderMuted}`,
          fontSize: 8, fontFamily: tokens.fontMono, color: tokens.textMuted,
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        }}>
          {id}
        </div>
      </div>

      <Handle type="source" position={Position.Right} style={{
        background: isDone ? tokens.green : tokens.accentDim,
        border: `1.5px solid ${isSelected ? tokens.accent : '#2a3050'}`,
        width: 8, height: 8, right: -4,
      }} />
    </>
  );
}

export const TaskNode = memo(TaskNodeComponent);
