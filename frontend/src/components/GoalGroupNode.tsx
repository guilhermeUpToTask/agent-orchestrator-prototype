import React from 'react';
import type { NodeProps } from '@xyflow/react';
import { Handle, Position } from '@xyflow/react';
import { ExternalLink, RotateCw } from 'lucide-react';
import { tokens, GOAL_STATUS_META } from '../styles/tokens';
import type { GoalGroupData } from '../lib/layout';
import { useRetryGoalFailed } from '../lib/queries';

/**
 * Group node that visually contains a goal's task nodes.
 * Cross-goal dependency edges attach to this node's handles, so they are
 * rendered goal-to-goal — clearly distinct from within-goal task edges.
 */
export function GoalGroupNode({ data }: NodeProps) {
  const { goal, color, phaseIndex, inActivePhase } = data as GoalGroupData;
  const statusMeta = GOAL_STATUS_META[goal.status] ?? { label: goal.status.toUpperCase(), color: tokens.textMuted };

  const retryFailed = useRetryGoalFailed();
  // Retryable = terminal-but-unsuccessful (failed or canceled). A canceled task
  // also fails its goal, so offer retry whenever the goal itself has failed too.
  const failedCount = goal.tasks.filter(
    (t) => t.status === 'failed' || t.status === 'canceled',
  ).length;
  const canRetry = failedCount > 0 || goal.status === 'failed';

  // PR review gates take visual priority: goals blocked on a human PR
  // decision are highlighted purple; merged goals settle to green.
  const gateColor =
    goal.status === 'awaiting_pr_approval' ? tokens.purple
    : goal.status === 'approved' ? tokens.accent
    : goal.status === 'merged' || goal.status === 'completed' ? tokens.green
    : null;
  const borderColor = gateColor ?? (inActivePhase ? color : color + '55');
  const solid = gateColor !== null || inActivePhase;

  return (
    <div style={{
      width: '100%', height: '100%',
      background: `${color}08`,
      border: `1.5px ${solid ? 'solid' : 'dashed'} ${borderColor}`,
      borderRadius: tokens.r12,
      boxShadow: gateColor ? `0 0 18px ${gateColor}33` : inActivePhase ? `0 0 18px ${color}22` : 'none',
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
        {phaseIndex >= 0 && (
          <span style={{
            fontSize: 8, fontFamily: tokens.fontMono, padding: '1px 5px', borderRadius: 3,
            background: inActivePhase ? `${color}22` : '#1c2030',
            color: inActivePhase ? color : tokens.textMuted, flexShrink: 0,
          }}>
            P{phaseIndex}
          </span>
        )}
        <div style={{ flex: 1 }} />
        {canRetry && (
          <button
            onClick={(e) => { e.stopPropagation(); retryFailed.mutate(goal.goal_id); }}
            disabled={retryFailed.isPending}
            title={failedCount > 0 ? `Retry ${failedCount} failed/canceled task(s)` : 'Retry this failed goal'}
            style={{
              display: 'flex', alignItems: 'center', gap: 3, flexShrink: 0,
              fontSize: 8, fontFamily: tokens.fontMono, cursor: 'pointer',
              padding: '2px 6px', borderRadius: 4,
              background: tokens.red + '1a', border: `1px solid ${tokens.red}44`,
              color: tokens.red,
            }}
          >
            <RotateCw size={9} />
            retry{failedCount > 0 ? ` ${failedCount}` : ''}
          </button>
        )}
        <span style={{ fontSize: 8, fontFamily: tokens.fontMono, color: statusMeta.color, flexShrink: 0 }}>
          [{statusMeta.label}]
        </span>
        {goal.pr_html_url && (
          <a
            href={goal.pr_html_url}
            target="_blank"
            rel="noreferrer"
            onClick={(e) => e.stopPropagation()}
            title="Open GitHub PR"
            style={{ display: 'flex', alignItems: 'center', color: tokens.purple, flexShrink: 0 }}
          >
            <ExternalLink size={10} />
          </a>
        )}
      </div>

      {goal.tasks.length === 0 && (
        <div style={{
          padding: '10px 12px', fontSize: 9, fontFamily: tokens.fontMono,
          color: tokens.textMuted,
        }}>
          no tasks yet — planned
        </div>
      )}
    </div>
  );
}
