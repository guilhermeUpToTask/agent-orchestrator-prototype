import React, { useCallback, useEffect, useMemo } from 'react';
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  addEdge,
  type NodeTypes,
  type Node,
  type Connection,
  SelectionMode,
  Panel,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { tokens, STATUS_META, type StatusKey } from '../styles/tokens';
import { usePlannerStore } from '../store/plannerStore';
import { useAgents, useGoals, usePlan } from '../lib/queries';
import { TaskNode } from './TaskNode';
import { GoalGroupNode } from './GoalGroupNode';
import { PhaseTimeline } from './PhaseTimeline';
import { buildFlowFromGoals, GOAL_COLORS } from '../lib/layout';
import type { TaskNodeData } from '../types/ui';

// Register custom node types
const nodeTypes: NodeTypes = {
  taskNode: TaskNode as React.ComponentType<any>,
  goalGroup: GoalGroupNode as React.ComponentType<any>,
};

// ─── Goal group legend ─────────────────────────────────────────────────────────

function GoalLegend() {
  const { data: goals = [] } = useGoals();

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 5,
      padding: '10px 12px',
      background: tokens.panelBg + 'ee',
      border: `1px solid ${tokens.border}`,
      borderRadius: tokens.r8,
      backdropFilter: 'blur(8px)',
    }}>
      <div style={{ fontSize: 8, fontFamily: tokens.fontMono, color: tokens.textMuted, letterSpacing: '0.1em', marginBottom: 2 }}>
        GOALS
      </div>
      {goals.map((g, i) => (
        <div key={g.goal_id} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <div style={{
            width: 6, height: 6, borderRadius: '50%',
            background: GOAL_COLORS[i % GOAL_COLORS.length],
          }} />
          <span style={{ fontSize: 9, fontFamily: tokens.fontMono, color: tokens.textSecond }}>
            {g.name}
          </span>
          <span style={{
            fontSize: 8, fontFamily: tokens.fontMono,
            color: g.status === 'running' ? tokens.yellow : g.status === 'merged' ? tokens.green : tokens.textMuted,
            marginLeft: 2,
          }}>
            [{g.status}]
          </span>
        </div>
      ))}
    </div>
  );
}

// ─── Vision strip ──────────────────────────────────────────────────────────────

function VisionStrip() {
  const { data: plan } = usePlan();
  if (!plan) return null;

  return (
    <div style={{
      maxWidth: 360,
      padding: '8px 12px',
      background: tokens.panelBg + 'ee',
      border: `1px solid ${tokens.border}`,
      borderRadius: tokens.r8,
      backdropFilter: 'blur(8px)',
    }}>
      <div style={{ fontSize: 8, fontFamily: tokens.fontMono, color: tokens.textMuted, letterSpacing: '0.1em', marginBottom: 3 }}>
        VISION · v{plan.state_version}
      </div>
      <div style={{
        fontSize: 10, color: tokens.textSecond, lineHeight: 1.5,
        display: '-webkit-box',
        WebkitLineClamp: 2,
        WebkitBoxOrient: 'vertical',
        overflow: 'hidden',
      }}>
        {plan.vision}
      </div>
    </div>
  );
}

// ─── Main canvas ───────────────────────────────────────────────────────────────

export function PlanCanvas() {
  const selectNode = usePlannerStore((s) => s.selectNode);
  const ui = usePlannerStore((s) => s.ui);

  // NOTE: no `= []` defaults here — a fresh array per render would change
  // the useMemo deps every render and loop setNodes/setEdges forever while
  // the queries are still loading. Normalize inside the memo instead.
  const { data: goals } = useGoals();
  const { data: agents } = useAgents();
  const { data: plan } = usePlan();

  // Server state → flow graph. Recomputed when goals/agents/plan/layout change.
  const layout = useMemo(
    () => buildFlowFromGoals(goals ?? [], agents ?? [], ui.layoutDirection, plan ?? null),
    [goals, agents, plan, ui.layoutDirection],
  );

  const [nodes, setNodes, onNodesChange] = useNodesState(layout.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(layout.edges);

  // Re-sync local flow state whenever the derived layout changes
  useEffect(() => {
    setNodes(layout.nodes);
    setEdges(layout.edges);
  }, [layout, setNodes, setEdges]);

  const onConnect = useCallback(
    (connection: Connection) => setEdges((eds) => addEdge({ ...connection, type: 'smoothstep' }, eds)),
    [setEdges],
  );

  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      if (node.type === 'goalGroup') return; // groups frame tasks; not selectable
      selectNode(ui.selectedNodeId === node.id ? null : node.id);
    },
    [selectNode, ui.selectedNodeId],
  );

  const onPaneClick = useCallback(() => {
    selectNode(null);
  }, [selectNode]);

  return (
    <div style={{ flex: 1, position: 'relative', minWidth: 0, minHeight: 0 }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
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
          style: { strokeWidth: 1.5, stroke: tokens.border },
        }}
        style={{ background: tokens.bg }}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={24}
          size={1}
          color="#1c2030"
        />

        <Controls
          style={{ bottom: 20, left: 20 }}
          showInteractive={false}
        />

        <MiniMap
          style={{ bottom: 20, right: ui.detailPanelOpen ? 336 : 20 }}
          nodeColor={(n) => {
            if (n.type === 'goalGroup') return '#1c2030';
            const data = (n as Node<TaskNodeData>).data;
            const meta = STATUS_META[data?.task?.status as StatusKey];
            return meta?.dot ?? tokens.border;
          }}
          maskColor="rgba(11,13,18,0.7)"
          nodeStrokeWidth={0}
        />

        {/* Top-left: goal legend */}
        <Panel position="top-left">
          <GoalLegend />
        </Panel>

        {/* Top-center: phase timeline */}
        <Panel position="top-center">
          <PhaseTimeline />
        </Panel>

        {/* Bottom-right: vision */}
        <Panel position="bottom-right" style={{ right: ui.detailPanelOpen ? 336 : 20 }}>
          <VisionStrip />
        </Panel>
      </ReactFlow>
    </div>
  );
}
