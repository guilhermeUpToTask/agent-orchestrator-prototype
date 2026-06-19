import React, { useMemo, useState } from 'react';
import { ReactFlow, Background, BackgroundVariant, Controls } from '@xyflow/react';
import type { Edge, Node } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from 'dagre';
import { GitPullRequest, GitBranch } from 'lucide-react';

import { tokens } from '../styles/tokens';
import { useCommitGraph, useForgeCapabilities, usePullRequests } from '../lib/forgeQueries';
import { useProjectStore } from '../store/projectStore';
import type { CommitGraph, PullRequest } from '../types/forge';

const NODE_W = 220;
const NODE_H = 48;

const CHECK_COLOR: Record<string, string> = {
  success: tokens.green, failure: tokens.red, pending: tokens.yellow,
  neutral: tokens.textMuted, unknown: tokens.textMuted,
};
const STATE_COLOR: Record<string, string> = {
  open: tokens.green, draft: tokens.textMuted, merged: tokens.purple, closed: tokens.red,
};

/** Lay the commit DAG out top-to-bottom with dagre (handles merge multi-parents). */
function layoutGraph(graph: CommitGraph, highlightSha: string | null): { nodes: Node[]; edges: Edge[] } {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'TB', nodesep: 24, ranksep: 40 });
  g.setDefaultEdgeLabel(() => ({}));

  const present = new Set(graph.nodes.map((n) => n.sha));
  for (const c of graph.nodes) g.setNode(c.sha, { width: NODE_W, height: NODE_H });
  const edges: Edge[] = [];
  for (const c of graph.nodes) {
    for (const p of c.parents) {
      if (!present.has(p)) continue; // boundary/dangling — don't draw to a missing node
      g.setEdge(p, c.sha);
      edges.push({ id: `${p}->${c.sha}`, source: p, target: c.sha, type: 'smoothstep' });
    }
  }
  dagre.layout(g);

  const nodes: Node[] = graph.nodes.map((c) => {
    const pos = g.node(c.sha);
    const isHead = c.sha === graph.head_sha;
    const isHighlight = c.sha === highlightSha;
    return {
      id: c.sha,
      position: { x: (pos?.x ?? 0) - NODE_W / 2, y: (pos?.y ?? 0) - NODE_H / 2 },
      data: { label: (
        <div style={{ textAlign: 'left', fontSize: 11, lineHeight: 1.3 }}>
          <div style={{ fontFamily: tokens.fontMono, color: tokens.accent }}>
            {c.sha.slice(0, 7)}{c.parents.length > 1 ? ' ⑃' : ''}
          </div>
          <div style={{ color: tokens.textSecond, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: NODE_W - 24 }}>
            {c.summary}
          </div>
        </div>
      ) },
      style: {
        width: NODE_W, height: NODE_H,
        background: tokens.cardBg,
        border: `1px solid ${isHighlight ? tokens.accent : isHead ? tokens.green : tokens.border}`,
        borderRadius: tokens.r8, padding: '4px 8px',
      },
    };
  });
  return { nodes, edges };
}

function PRItem({ pr, onSelect, active }: { pr: PullRequest; onSelect: () => void; active: boolean }) {
  return (
    <button
      onClick={onSelect}
      style={{
        textAlign: 'left', width: '100%', background: active ? tokens.accentDim : tokens.cardBg,
        border: `1px solid ${active ? tokens.accent : tokens.border}`, borderRadius: tokens.r8,
        padding: '8px 10px', marginBottom: 8, cursor: 'pointer', color: tokens.textPrimary,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
        <span style={{ fontFamily: tokens.fontMono, color: tokens.textMuted }}>#{pr.number}</span>
        <span style={{ fontWeight: 600, flex: 1 }}>{pr.title}</span>
        <span style={{ fontSize: 10, color: STATE_COLOR[pr.state] ?? tokens.textMuted, fontFamily: tokens.fontMono }}>
          {pr.state}
        </span>
      </div>
      <div style={{ display: 'flex', gap: 10, fontSize: 10, fontFamily: tokens.fontMono, color: tokens.textMuted, marginTop: 4 }}>
        <span><GitBranch size={9} /> {pr.head_ref} → {pr.base_ref}</span>
        <span style={{ color: CHECK_COLOR[pr.checks] }}>checks: {pr.checks}</span>
        <span>review: {pr.review_state}</span>
        <span>{pr.is_mergeable === null ? 'mergeable: checking…' : `mergeable: ${pr.is_mergeable}`}</span>
      </div>
    </button>
  );
}

export function PullRequestsView() {
  const activeProjectId = useProjectStore((s) => s.activeProjectId);
  const { data: graph } = useCommitGraph();
  const { data: prs = [] } = usePullRequests();
  const { data: caps } = useForgeCapabilities();
  const [selectedPr, setSelectedPr] = useState<number | null>(null);

  const highlightSha = useMemo(() => {
    if (selectedPr === null) return null;
    return prs.find((p) => p.number === selectedPr)?.head_sha ?? null;
  }, [selectedPr, prs]);

  const { nodes, edges } = useMemo(
    () => (graph ? layoutGraph(graph, highlightSha) : { nodes: [], edges: [] }),
    [graph, highlightSha],
  );

  if (!activeProjectId) {
    return (
      <div style={{ padding: 18, color: tokens.textMuted, fontSize: 12 }}>
        Select a project to view its pull requests and commit history.
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', height: '100%' }}>
      <div style={{ width: 360, borderRight: `1px solid ${tokens.border}`, padding: 14, overflowY: 'auto' }}>
        <h2 style={{ fontSize: 13, fontFamily: tokens.fontMono, color: tokens.textPrimary, letterSpacing: '0.06em', marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
          <GitPullRequest size={15} /> PULL REQUESTS
        </h2>
        {caps && !caps.supports_prs && (
          <p style={{ fontSize: 11, color: tokens.yellow, fontFamily: tokens.fontMono, marginBottom: 10 }}>
            Local-git source — PRs unavailable. Configure a GitHub token in Settings.
          </p>
        )}
        {prs.length === 0 && (
          <p style={{ fontSize: 12, color: tokens.textMuted }}>No open pull requests.</p>
        )}
        {prs.map((pr) => (
          <PRItem key={pr.number} pr={pr} active={pr.number === selectedPr} onSelect={() => setSelectedPr(pr.number)} />
        ))}
      </div>

      <div style={{ flex: 1, position: 'relative' }}>
        {graph && graph.truncated && (
          <div style={{ position: 'absolute', top: 8, right: 8, zIndex: 5, fontSize: 10, color: tokens.textMuted, fontFamily: tokens.fontMono }}>
            history truncated
          </div>
        )}
        <ReactFlow nodes={nodes} edges={edges} fitView proOptions={{ hideAttribution: true }}>
          <Background variant={BackgroundVariant.Dots} gap={20} size={1} color={tokens.border} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  );
}
