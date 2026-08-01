import React from 'react';
import { Navigate, NavLink, Route, Routes } from 'react-router-dom';
import { BrainCircuit, Boxes, Bot, TerminalSquare, Wrench, FolderGit2, ListChecks, Rocket } from 'lucide-react';
import { SetupSection } from './SetupSection';
import { ReasonerSection } from './ReasonerSection';
import { RunnerSection } from './RunnerSection';
import { ProvidersSection } from './ProvidersSection';
import { AgentsSection } from './AgentsSection';
import { CapabilitiesSection } from './CapabilitiesSection';
import { ProjectsSection } from './ProjectsSection';
import { ReadinessSection } from './ReadinessSection';
import styles from './Settings.module.css';

const SECTIONS = [
  { path: 'setup', label: 'Get started', Icon: Rocket },
  { path: 'readiness', label: 'Readiness', Icon: ListChecks },
  { path: 'reasoner', label: 'Reasoner', Icon: BrainCircuit },
  { path: 'runner', label: 'Agent runtime', Icon: TerminalSquare },
  { path: 'providers', label: 'Providers & models', Icon: Boxes },
  { path: 'agents', label: 'Agents', Icon: Bot },
  { path: 'capabilities', label: 'Capabilities', Icon: Wrench },
  { path: 'projects', label: 'Projects', Icon: FolderGit2 },
] as const;

/**
 * The machine room: everything the API exposes for configuration —
 * reasoner wiring, the providers/models catalog, the agent roster,
 * capabilities and projects — each editable in place.
 */
export function SettingsLayout() {
  return (
    <div className={styles.page}>
      <nav className={styles.nav} aria-label="Settings sections">
        <div className={`label ${styles.navTitle}`}>Settings</div>
        {SECTIONS.map(({ path, label, Icon }) => (
          <NavLink
            key={path}
            to={path}
            className={({ isActive }) =>
              `${styles.navLink} ${isActive ? styles.navActive : ''}`
            }
          >
            <Icon size={14} aria-hidden />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className={styles.content}>
        <Routes>
          <Route index element={<Navigate to="readiness" replace />} />
          <Route path="setup" element={<SetupSection />} />
          <Route path="readiness" element={<ReadinessSection />} />
          <Route path="reasoner" element={<ReasonerSection />} />
          <Route path="runner" element={<RunnerSection />} />
          <Route path="providers" element={<ProvidersSection />} />
          <Route path="agents" element={<AgentsSection />} />
          <Route path="capabilities" element={<CapabilitiesSection />} />
          <Route path="projects" element={<ProjectsSection />} />
          <Route path="*" element={<Navigate to="readiness" replace />} />
        </Routes>
      </div>
    </div>
  );
}
