import React, { useCallback } from 'react';
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  type NodeTypes,
  type Node,
  type Edge,
  SelectionMode,
  Panel,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { tokens, STATUS_META, AGENT_COLORS, type StatusKey } from '../styles/tokens';
import { usePlannerStore } from '../store/plannerStore';
import { TaskNode } from './TaskNode';
import { GoalGroupNode } from './GoalGroupNode';
import { PhaseTimeline } from './PhaseTimeline';
import type { TaskNodeData } from '../types/ui';

// Register custom node types
const nodeTypes: NodeTypes = {
  taskNode: TaskNode as React.ComponentType<any>,
  goalGroup: GoalGroupNode as React.ComponentType<any>,
};

// ─── Goal group legend ─────────────────────────────────────────────────────────

function GoalLegend() {
  const goals = usePlannerStore((s) => s.goals);
  const GOAL_COLORS = [tokens.accent, tokens.purple, tokens.cyan, tokens.green, tokens.orange];

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
  const plan = usePlannerStore((s) => s.plan);

  // Guard: plan is null until loadPlan() resolves — render nothing while loading
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
  const nodes = usePlannerStore((s) => s.nodes);
  const edges = usePlannerStore((s) => s.edges);
  const onNodesChange = usePlannerStore((s) => s.onNodesChange);
  const onEdgesChange = usePlannerStore((s) => s.onEdgesChange);
  const onConnect = usePlannerStore((s) => s.onConnect);
  const selectNode = usePlannerStore((s) => s.selectNode);
  const ui = usePlannerStore((s) => s.ui);

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
