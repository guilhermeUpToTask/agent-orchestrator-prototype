import dagre from 'dagre';
import type { Node, Edge } from '@xyflow/react';
import type { TaskNodeData, GoalAggregate, AgentProps, ProjectPlan } from '../types/ui';
import { tokens } from '../styles/tokens';

const NODE_W = 240;
const NODE_H = 160;

// Goal group chrome
const GROUP_PAD = 24;
const GROUP_HEADER = 44;
const EMPTY_GROUP_W = 280;
const EMPTY_GROUP_H = 96;

export const GOAL_COLORS = [tokens.accent, tokens.purple, tokens.cyan, tokens.green, tokens.orange];

export interface GoalGroupData {
  goal: GoalAggregate;
  color: string;
  /** Index of the phase this goal belongs to, -1 when unknown */
  phaseIndex: number;
  /** True when the goal belongs to the currently active phase */
  inActivePhase: boolean;
  [key: string]: unknown;
}

export const goalNodeId = (goalId: string) => `goal-${goalId}`;

function phaseIndexOf(goal: GoalAggregate, plan: ProjectPlan | null): number {
  if (!plan) return -1;
  const phase = plan.phases.find((p) => p.goal_names.includes(goal.name));
  return phase ? phase.index : -1;
}

/**
 * Two-level layout:
 *   1. Each goal's tasks are laid out with dagre inside the goal's group node.
 *   2. The goal groups themselves are laid out with dagre using
 *      GoalAggregate.depends_on as the meta-graph edges.
 *
 * Task nodes are React Flow children (parentId + extent) of their goal group,
 * so users can visually distinguish which goal a task belongs to. Cross-goal
 * dependencies are rendered as distinct highlighted edges between group nodes,
 * while within-goal task succession stays as thin task→task edges.
 */
export function buildFlowFromGoals(
  goals: GoalAggregate[],
  agents: AgentProps[],
  direction: 'LR' | 'TB' = 'LR',
  plan: ProjectPlan | null = null,
): { nodes: Node[]; edges: Edge[] } {
  const rfNodes: Node[] = [];
  const rfEdges: Edge[] = [];
  const agentMap = new Map(agents.map((a) => [a.agent_id, a]));

  // ── Pass 1: lay out tasks inside each goal, record group sizes ────────────
  const groupSize = new Map<string, { w: number; h: number }>();
  const taskPos = new Map<string, { x: number; y: number }>();

  for (const goal of goals) {
    if (goal.tasks.length === 0) {
      groupSize.set(goal.goal_id, { w: EMPTY_GROUP_W, h: EMPTY_GROUP_H });
      continue;
    }
    const g = new dagre.graphlib.Graph();
    g.setDefaultEdgeLabel(() => ({}));
    g.setGraph({ rankdir: direction, ranksep: 60, nodesep: 32, marginx: GROUP_PAD, marginy: GROUP_PAD });
    for (const task of goal.tasks) g.setNode(task.task_id, { width: NODE_W, height: NODE_H });
    for (let i = 1; i < goal.tasks.length; i++) {
      g.setEdge(goal.tasks[i - 1].task_id, goal.tasks[i].task_id);
    }
    dagre.layout(g);

    let maxX = 0;
    let maxY = 0;
    for (const task of goal.tasks) {
      const pos = g.node(task.task_id);
      const x = pos.x - NODE_W / 2;
      const y = pos.y - NODE_H / 2;
      taskPos.set(task.task_id, { x, y: y + GROUP_HEADER });
      maxX = Math.max(maxX, x + NODE_W);
      maxY = Math.max(maxY, y + NODE_H);
    }
    groupSize.set(goal.goal_id, {
      w: maxX + GROUP_PAD,
      h: maxY + GROUP_HEADER + GROUP_PAD,
    });
  }

  // ── Pass 2: lay out the goal groups using cross-goal dependencies ─────────
  const meta = new dagre.graphlib.Graph();
  meta.setDefaultEdgeLabel(() => ({}));
  meta.setGraph({ rankdir: direction, ranksep: 110, nodesep: 64, marginx: 48, marginy: 48 });

  for (const goal of goals) {
    const size = groupSize.get(goal.goal_id)!;
    meta.setNode(goalNodeId(goal.goal_id), { width: size.w, height: size.h });
  }
  for (const goal of goals) {
    for (const depGoalName of goal.depends_on) {
      const depGoal = goals.find((g) => g.name === depGoalName);
      if (!depGoal) continue;
      meta.setEdge(goalNodeId(depGoal.goal_id), goalNodeId(goal.goal_id));
    }
  }
  dagre.layout(meta);

  // ── Emit nodes: group first (React Flow requires parents before children) ──
  goals.forEach((goal, i) => {
    const size = groupSize.get(goal.goal_id)!;
    const pos = meta.node(goalNodeId(goal.goal_id));
    const phaseIndex = phaseIndexOf(goal, plan);
    const data: GoalGroupData = {
      goal,
      color: GOAL_COLORS[i % GOAL_COLORS.length],
      phaseIndex,
      inActivePhase: plan != null && phaseIndex === plan.current_phase_index,
    };
    rfNodes.push({
      id: goalNodeId(goal.goal_id),
      type: 'goalGroup',
      position: { x: pos.x - size.w / 2, y: pos.y - size.h / 2 },
      style: { width: size.w, height: size.h },
      data,
      draggable: true,
      selectable: false,
      zIndex: -1,
    });

    // Sibling tasks that have finished — a CREATED task waiting on any unfinished
    // dependency is "blocked", not merely queued.
    const doneIds = new Set(
      goal.tasks.filter((t) => t.status === 'succeeded' || t.status === 'merged').map((t) => t.task_id),
    );
    for (const task of goal.tasks) {
      const blockedBy =
        task.status === 'created' ? task.depends_on.filter((d) => !doneIds.has(d)) : [];
      rfNodes.push({
        id: task.task_id,
        type: 'taskNode',
        parentId: goalNodeId(goal.goal_id),
        extent: 'parent',
        position: taskPos.get(task.task_id)!,
        data: {
          task,
          goalId: goal.goal_id,
          goalName: goal.name,
          agent: task.assigned_agent_id ? (agentMap.get(task.assigned_agent_id) ?? null) : null,
          blockedBy,
          selected: false,
        } satisfies TaskNodeData,
      });
    }
  });

  // ── Within-goal edges from task succession ────────────────────────────────
  for (const goal of goals) {
    for (let i = 1; i < goal.tasks.length; i++) {
      const src = goal.tasks[i - 1].task_id;
      const dst = goal.tasks[i].task_id;
      const active = ['succeeded', 'merged'].includes(goal.tasks[i - 1].status);
      rfEdges.push(makeTaskEdge(src, dst, active));
    }
  }

  // ── Cross-goal edges between group nodes (distinct styling) ───────────────
  for (const goal of goals) {
    for (const depGoalName of goal.depends_on) {
      const depGoal = goals.find((g) => g.name === depGoalName);
      if (!depGoal) continue;
      const src = goalNodeId(depGoal.goal_id);
      const dst = goalNodeId(goal.goal_id);
      const satisfied = depGoal.status === 'merged' || depGoal.status === 'completed';
      if (!rfEdges.some((e) => e.source === src && e.target === dst)) {
        rfEdges.push(makeGoalEdge(src, dst, satisfied));
      }
    }
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
    label: satisfied ? 'dependency met' : 'depends on',
    labelStyle: {
      fontSize: 9,
      fill: satisfied ? tokens.green : tokens.purple,
      fontFamily: tokens.fontMono,
    },
    labelBgStyle: { fill: '#0b0d12', fillOpacity: 0.85 },
    data: { active: satisfied, crossGoal: true },
  };
}
