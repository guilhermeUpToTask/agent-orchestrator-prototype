import React, { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import type { TaskNodeData } from '../types/domain';
import { tokens, STATUS_META, AGENT_COLORS, type StatusKey } from '../styles/tokens';
import { usePlannerStore } from '../store/plannerStore';

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

function TaskNodeComponent({ id, data }: NodeProps<TaskNodeData>) {
  const selectNode = usePlannerStore((s) => s.selectNode);
  const selectedNodeId = usePlannerStore((s) => s.ui.selectedNodeId);
  const isSelected = selectedNodeId === id;

  const status = (data.task?.status ?? 'created') as StatusKey;
  const meta = STATUS_META[status] ?? STATUS_META.created;
  const agentColor = data.agent ? (AGENT_COLORS[data.agent.name] ?? tokens.textSecond) : tokens.textMuted;
  const isRunning = status === 'in_progress' || status === 'assigned';
  const isSucceeded = status === 'succeeded' || status === 'merged';
  const isFailed = status === 'failed' || status === 'canceled';

  return (
    <>
      <Handle type="target" position={Position.Left} style={{
        background: tokens.accentDim, border: `1.5px solid ${isSelected ? tokens.accent : '#2a3050'}`,
        width: 8, height: 8, left: -4,
      }} />

      <div onClick={() => selectNode(isSelected ? null : id)} style={{
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
      }}>
        {/* Status stripe */}
        <div style={{
          height: 3,
          background: isSucceeded ? `linear-gradient(90deg, ${tokens.green}, transparent)`
            : isRunning ? `linear-gradient(90deg, ${tokens.yellow}, transparent)`
            : isFailed ? `linear-gradient(90deg, ${tokens.red}, transparent)`
            : `linear-gradient(90deg, ${agentColor}66, transparent)`,
        }} />

        {/* Header */}
        <div style={{
          padding: '7px 10px 5px', borderBottom: `1px solid ${tokens.borderMuted}`,
          display: 'flex', alignItems: 'center', gap: 6, background: meta.bg,
        }}>
          <PulsingDot color={meta.dot} />
          <span style={{ fontSize: 9, fontFamily: tokens.fontMono, color: meta.color, letterSpacing: '0.1em', fontWeight: 600 }}>
            {meta.label}
          </span>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
            <span style={{
              fontSize: 8, fontFamily: tokens.fontMono, padding: '1px 5px',
              borderRadius: 3, background: '#1c2030', color: tokens.textMuted,
            }}>{data.goalName}</span>
            {data.agent && (
              <span style={{
                fontSize: 8, fontFamily: tokens.fontMono, padding: '1px 5px',
                borderRadius: 3, background: agentColor + '18',
                border: `1px solid ${agentColor}33`, color: agentColor,
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
            {data.task?.title ?? id}
          </div>

          {/* Retry count */}
          {(data.task?.retry_count ?? 0) > 0 && (
            <div style={{
              fontSize: 9, fontFamily: tokens.fontMono,
              color: tokens.yellow, marginBottom: 4,
            }}>
              ↺ retry {data.task.retry_count}
            </div>
          )}

          {/* Running dots */}
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

          {isSucceeded && (
            <div style={{
              padding: '3px 7px', borderRadius: 5,
              background: tokens.greenDim, border: `1px solid ${tokens.green}33`,
              fontSize: 9, fontFamily: tokens.fontMono, color: tokens.green,
            }}>
              ✓ {status === 'merged' ? 'merged to main' : 'succeeded'}
            </div>
          )}

          {isFailed && (
            <div style={{
              padding: '3px 7px', borderRadius: 5,
              background: tokens.redDim, border: `1px solid ${tokens.red}33`,
              fontSize: 9, fontFamily: tokens.fontMono, color: tokens.red,
            }}>
              ✗ {status}
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{
          padding: '3px 10px', borderTop: `1px solid ${tokens.borderMuted}`,
          fontSize: 8, fontFamily: tokens.fontMono, color: tokens.textMuted,
        }}>
          {id}
        </div>
      </div>

      <Handle type="source" position={Position.Right} style={{
        background: isSucceeded ? tokens.green : tokens.accentDim,
        border: `1.5px solid ${isSelected ? tokens.accent : '#2a3050'}`,
        width: 8, height: 8, right: -4,
      }} />
    </>
  );
}

export const TaskNode = memo(TaskNodeComponent);
