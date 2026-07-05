import React, { useCallback, useEffect, useMemo } from 'react';
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  type NodeTypes,
  type Node,
  SelectionMode,
  Panel,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { tokens, STATUS, raw } from '../styles/tokens';
import { usePlannerStore } from '../store/plannerStore';
import { useAgents, usePlan } from '../lib/queries';
import { TaskNode } from './TaskNode';
import { GoalGroupNode } from './GoalGroupNode';
import { PhaseTimeline } from './PhaseTimeline';
import { buildFlowFromGoals, GOAL_COLORS } from '../lib/layout';
import type { Plan, TaskNodeData } from '../types/ui';

const nodeTypes: NodeTypes = {
  taskNode: TaskNode as React.ComponentType<any>,
  goalGroup: GoalGroupNode as React.ComponentType<any>,
};

const KIND_COLOR = {
  idle: raw.idle, run: raw.run, gate: raw.gate, ok: raw.ok, fail: raw.fail,
} as const;

// ─── Goal group legend ─────────────────────────────────────────────────────────

function GoalLegend({ plan }: { plan: Plan | undefined }) {
  const goals = plan?.goals ?? [];
  if (goals.length === 0) return null;
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 5,
      padding: '10px 12px',
      background: raw.bg1 + 'ee', // hex+alpha concat — must stay raw
      border: `1px solid ${tokens.border}`,
      borderRadius: tokens.r8,
      backdropFilter: 'blur(8px)',
    }}>
      <div style={{ fontSize: 8, fontFamily: tokens.fontMono, color: tokens.textMuted, letterSpacing: '0.1em', marginBottom: 2 }}>
        GOALS
      </div>
      {goals.slice().sort((a, b) => a.position - b.position).map((g, i) => (
        <div key={g.id} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <div style={{
            width: 6, height: 6, borderRadius: '50%',
            background: GOAL_COLORS[i % GOAL_COLORS.length],
          }} />
          <span style={{ fontSize: 9, fontFamily: tokens.fontMono, color: tokens.textSecond }}>
            {g.name}
          </span>
          <span style={{
            fontSize: 8, fontFamily: tokens.fontMono,
            color: KIND_COLOR[(STATUS[g.status] ?? STATUS.pending).kind],
            marginLeft: 2,
          }}>
            [{g.status}]
          </span>
        </div>
      ))}
    </div>
  );
}

// ─── Brief strip ───────────────────────────────────────────────────────────────

function BriefStrip({ plan }: { plan: Plan | undefined }) {
  if (!plan) return null;
  return (
    <div style={{
      maxWidth: 360,
      padding: '8px 12px',
      background: raw.bg1 + 'ee', // hex+alpha concat — must stay raw
      border: `1px solid ${tokens.border}`,
      borderRadius: tokens.r8,
      backdropFilter: 'blur(8px)',
    }}>
      <div style={{ fontSize: 8, fontFamily: tokens.fontMono, color: tokens.textMuted, letterSpacing: '0.1em', marginBottom: 3 }}>
        BRIEF · v{plan.version} · iter {plan.iteration}
      </div>
      <div style={{
        fontSize: 10, color: tokens.textSecond, lineHeight: 1.5,
        display: '-webkit-box',
        WebkitLineClamp: 2,
        WebkitBoxOrient: 'vertical',
        overflow: 'hidden',
      }}>
        {plan.brief}
      </div>
    </div>
  );
}

// ─── Main canvas ───────────────────────────────────────────────────────────────

export function PlanCanvas({ planId }: { planId: string }) {
  const selectTask = usePlannerStore((s) => s.selectTask);
  const ui = usePlannerStore((s) => s.ui);

  // NOTE: no `= []` defaults here — a fresh array per render would change
  // the useMemo deps every render and loop setNodes/setEdges forever while
  // the queries are still loading. Normalize inside the memo instead.
  const { data: plan } = usePlan(planId);
  const { data: agents } = useAgents();

  const layout = useMemo(
    () => buildFlowFromGoals(plan?.goals ?? [], agents ?? [], ui.layoutDirection),
    [plan, agents, ui.layoutDirection],
  );

  const [nodes, setNodes, onNodesChange] = useNodesState(layout.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(layout.edges);

  useEffect(() => {
    setNodes(layout.nodes);
    setEdges(layout.edges);
  }, [layout, setNodes, setEdges]);

  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      if (node.type === 'goalGroup') return; // groups frame tasks; not selectable
      selectTask(ui.selectedTaskId === node.id ? null : node.id);
    },
    [selectTask, ui.selectedTaskId],
  );

  const onPaneClick = useCallback(() => {
    selectTask(null);
  }, [selectTask]);

  return (
    <div style={{ flex: 1, position: 'relative', minWidth: 0, minHeight: 0 }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={onNodeClick}
        onPaneClick={onPaneClick}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.15 }}
        minZoom={0.2}
        maxZoom={2}
        selectionMode={SelectionMode.Partial}
        proOptions={{ hideAttribution: true }}
        defaultEdgeOptions={{
          type: 'smoothstep',
          style: { strokeWidth: 1.5, stroke: raw.border0 },
        }}
        style={{ background: tokens.bg }}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={24}
          size={1}
          color="#1c2030"
        />

        <Controls style={{ bottom: 20, left: 20 }} showInteractive={false} />

        <MiniMap
          style={{ bottom: 20, right: ui.detailPanelOpen ? 336 : 20 }}
          nodeColor={(n) => {
            if (n.type === 'goalGroup') return '#1c2030';
            const data = (n as Node<TaskNodeData>).data;
            const meta = STATUS[data?.task?.status ?? 'pending'];
            return meta ? KIND_COLOR[meta.kind] : raw.border0;
          }}
          maskColor="rgba(11,13,18,0.7)"
          nodeStrokeWidth={0}
        />

        <Panel position="top-left">
          <GoalLegend plan={plan} />
        </Panel>

        <Panel position="top-center">
          {plan && <PhaseTimeline phase={plan.phase} iteration={plan.iteration} />}
        </Panel>

        <Panel position="bottom-right" style={{ right: ui.detailPanelOpen ? 336 : 20 }}>
          <BriefStrip plan={plan} />
        </Panel>
      </ReactFlow>
    </div>
  );
}
