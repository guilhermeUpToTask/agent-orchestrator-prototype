import React from 'react';
import type { NodeProps } from '@xyflow/react';
import { Handle, Position } from '@xyflow/react';
import { tokens, STATUS, raw } from '../styles/tokens';
import type { GoalGroupData } from '../lib/layout';

const KIND_COLOR = {
  idle: raw.idle, run: raw.run, gate: raw.gate, ok: raw.ok, fail: raw.fail,
} as const;

/**
 * Group node that visually contains a goal's task nodes. Goal-to-goal
 * succession edges attach to this node's handles.
 */
export function GoalGroupNode({ data }: NodeProps) {
  const { goal, color } = data as GoalGroupData;
  const meta = STATUS[goal.status] ?? STATUS.pending;
  const statusColor = KIND_COLOR[meta.kind];

  const settled = goal.status === 'done';
  const active = goal.status === 'running';
  const closed = goal.status === 'skipped' || goal.status === 'failed';

  const borderColor = settled ? tokens.green : active ? color : color + '55';

  return (
    <div style={{
      width: '100%', height: '100%',
      background: `${color}08`,
      border: `1.5px ${active || settled ? 'solid' : 'dashed'} ${borderColor}`,
      borderRadius: tokens.r12,
      boxShadow: active ? `0 0 18px ${color}22` : 'none',
      opacity: closed ? 0.55 : 1,
    }}>
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />

      {/* Header strip */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '8px 12px',
        borderBottom: `1px solid ${color}22`,
      }}>
        <div style={{ width: 7, height: 7, borderRadius: '50%', background: color, flexShrink: 0 }} />
        <span style={{
          fontSize: 11, fontFamily: tokens.fontMono, color: tokens.textPrimary,
          letterSpacing: '0.04em', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        }}>
          {goal.name}
        </span>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 8, fontFamily: tokens.fontMono, color: statusColor, flexShrink: 0 }}>
          [{meta.label.toUpperCase()}]
        </span>
      </div>

      {goal.tasks.length === 0 && (
        <div style={{
          padding: '10px 12px', fontSize: 9, fontFamily: tokens.fontMono,
          color: tokens.textMuted,
        }}>
          no tasks yet — the enriching phase populates this goal
        </div>
      )}
    </div>
  );
}
