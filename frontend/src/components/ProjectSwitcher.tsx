import React from 'react';
import { FolderGit2 } from 'lucide-react';

import { useActivateProject, useProjects } from '../lib/controlQueries';
import { useProjectStore } from '../store/projectStore';
import { tokens } from '../styles/tokens';

/**
 * App-wide project switcher. Selecting a project activates it on the backend
 * and refetches all scoped data (handled in useActivateProject).
 */
export function ProjectSwitcher() {
  const { data: projects = [] } = useProjects();
  const activeId = useProjectStore((s) => s.activeProjectId);
  const activate = useActivateProject();

  if (projects.length === 0) return null;

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <FolderGit2 size={13} color={tokens.textMuted} aria-hidden />
      <select
        aria-label="Active project"
        value={activeId ?? ''}
        onChange={(e) => activate.mutate(e.target.value)}
        style={{
          background: tokens.inputBg,
          border: `1px solid ${tokens.border}`,
          borderRadius: tokens.r8,
          color: tokens.textPrimary,
          fontSize: 12,
          fontFamily: tokens.fontMono,
          padding: '4px 8px',
        }}
      >
        {activeId === null && <option value="">— select project —</option>}
        {projects.map((p) => (
          <option key={p.id} value={p.id}>{p.name}</option>
        ))}
      </select>
    </div>
  );
}
