import dagre from 'dagre';
import type { Node, Edge } from '@xyflow/react';
import type { AgentSpec, Goal, TaskNodeData } from '../types/ui';
import { tokens } from '../styles/tokens';

const NODE_W = 240;
const NODE_H = 130;

// Goal group chrome
const GROUP_PAD = 24;
const GROUP_HEADER = 44;
const EMPTY_GROUP_W = 280;
const EMPTY_GROUP_H = 96;

export const GOAL_COLORS = [tokens.accent, tokens.purple, tokens.cyan, tokens.green, tokens.orange];

export interface GoalGroupData {
  goal: Goal;
  color: string;
  [key: string]: unknown;
}

export const goalNodeId = (goalId: string) => `goal-${goalId}`;

/**
 * Two-level layout:
 *   1. Each goal's tasks are laid out with dagre inside the goal's group node
 *      (execution is sequential by `position`, so the edges chain them).
 *   2. The goal groups themselves are laid out with dagre chained by
 *      `position` (the plan executes goals in order).
 *
 * Task nodes are React Flow children (parentId + extent) of their goal group,
 * so users can visually distinguish which goal a task belongs to.
 */
export function buildFlowFromGoals(
  goals: Goal[],
  agents: AgentSpec[],
  direction: 'LR' | 'TB' = 'LR',
): { nodes: Node[]; edges: Edge[] } {
  const rfNodes: Node[] = [];
  const rfEdges: Edge[] = [];
  const agentMap = new Map(agents.map((a) => [a.id, a]));
  const ordered = goals.slice().sort((a, b) => a.position - b.position);

  // ── Pass 1: lay out tasks inside each goal, record group sizes ────────────
  const groupSize = new Map<string, { w: number; h: number }>();
  const taskPos = new Map<string, { x: number; y: number }>();

  for (const goal of ordered) {
    const tasks = goal.tasks.slice().sort((a, b) => a.position - b.position);
    if (tasks.length === 0) {
      groupSize.set(goal.id, { w: EMPTY_GROUP_W, h: EMPTY_GROUP_H });
      continue;
    }
    const g = new dagre.graphlib.Graph();
    g.setDefaultEdgeLabel(() => ({}));
    g.setGraph({ rankdir: direction, ranksep: 60, nodesep: 32, marginx: GROUP_PAD, marginy: GROUP_PAD });
    for (const task of tasks) g.setNode(task.id, { width: NODE_W, height: NODE_H });
    for (let i = 1; i < tasks.length; i++) {
      g.setEdge(tasks[i - 1].id, tasks[i].id);
    }
    dagre.layout(g);

    let maxX = 0;
    let maxY = 0;
    for (const task of tasks) {
      const pos = g.node(task.id);
      const x = pos.x - NODE_W / 2;
      const y = pos.y - NODE_H / 2;
      taskPos.set(task.id, { x, y: y + GROUP_HEADER });
      maxX = Math.max(maxX, x + NODE_W);
      maxY = Math.max(maxY, y + NODE_H);
    }
    groupSize.set(goal.id, {
      w: maxX + GROUP_PAD,
      h: maxY + GROUP_HEADER + GROUP_PAD,
    });
  }

  // ── Pass 2: lay out the goal groups chained by position ───────────────────
  const meta = new dagre.graphlib.Graph();
  meta.setDefaultEdgeLabel(() => ({}));
  meta.setGraph({ rankdir: direction, ranksep: 110, nodesep: 64, marginx: 48, marginy: 48 });

  for (const goal of ordered) {
    const size = groupSize.get(goal.id)!;
    meta.setNode(goalNodeId(goal.id), { width: size.w, height: size.h });
  }
  for (let i = 1; i < ordered.length; i++) {
    meta.setEdge(goalNodeId(ordered[i - 1].id), goalNodeId(ordered[i].id));
  }
  dagre.layout(meta);

  // ── Emit nodes: group first (React Flow requires parents before children) ──
  ordered.forEach((goal, i) => {
    const size = groupSize.get(goal.id)!;
    const pos = meta.node(goalNodeId(goal.id));
    const data: GoalGroupData = {
      goal,
      color: GOAL_COLORS[i % GOAL_COLORS.length],
    };
    rfNodes.push({
      id: goalNodeId(goal.id),
      type: 'goalGroup',
      position: { x: pos.x - size.w / 2, y: pos.y - size.h / 2 },
      style: { width: size.w, height: size.h },
      data,
      draggable: true,
      selectable: false,
      zIndex: -1,
    });

    for (const task of goal.tasks.slice().sort((a, b) => a.position - b.position)) {
      rfNodes.push({
        id: task.id,
        type: 'taskNode',
        parentId: goalNodeId(goal.id),
        extent: 'parent',
        position: taskPos.get(task.id)!,
        data: {
          task,
          goalId: goal.id,
          goalName: goal.name,
          agent: task.agent_id ? (agentMap.get(task.agent_id) ?? null) : null,
          selected: false,
        } satisfies TaskNodeData,
      });
    }
  });

  // ── Within-goal edges from sequential position order ──────────────────────
  for (const goal of ordered) {
    const tasks = goal.tasks.slice().sort((a, b) => a.position - b.position);
    for (let i = 1; i < tasks.length; i++) {
      const active = tasks[i - 1].status === 'done';
      rfEdges.push(makeTaskEdge(tasks[i - 1].id, tasks[i].id, active));
    }
  }

  // ── Goal-to-goal succession edges ──────────────────────────────────────────
  for (let i = 1; i < ordered.length; i++) {
    const satisfied = ordered[i - 1].status === 'done';
    rfEdges.push(makeGoalEdge(goalNodeId(ordered[i - 1].id), goalNodeId(ordered[i].id), satisfied));
  }

  return { nodes: rfNodes, edges: rfEdges };
}

function makeTaskEdge(src: string, dst: string, active: boolean): Edge {
  return {
    id: `edge-${src}-${dst}`,
    source: src,
    target: dst,
    type: 'smoothstep',
    animated: active,
    style: {
      stroke: active ? tokens.accent : tokens.border,
      strokeWidth: 1.5,
      strokeDasharray: active ? undefined : '5 4',
    },
    data: { active, crossGoal: false },
  };
}

function makeGoalEdge(src: string, dst: string, satisfied: boolean): Edge {
  return {
    id: `edge-${src}-${dst}`,
    source: src,
    target: dst,
    type: 'smoothstep',
    animated: !satisfied,
    style: {
      stroke: satisfied ? tokens.green : tokens.purple,
      strokeWidth: 2.5,
      strokeDasharray: satisfied ? undefined : '8 5',
    },
    data: { active: satisfied, crossGoal: true },
  };
}
