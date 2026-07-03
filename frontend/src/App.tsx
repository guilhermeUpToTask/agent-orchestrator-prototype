import React from 'react';
import { BrowserRouter, Navigate, Route, Routes, useParams } from 'react-router-dom';
import { TopBar } from './components/TopBar';
import { LifecycleRail } from './components/LifecycleRail';
import { GatePanel } from './components/GatePanel';
import { ChatPanel } from './components/ChatPanel';
import { ConsoleDock } from './components/ConsoleDock';
import { Toaster } from './components/Toaster';
import { Overview } from './views/Overview';
import { GoalsView } from './views/Goals';
import { ActivityView } from './views/Activity';
import { AgentsView } from './views/Agents';
import { SettingsView } from './views/Settings';
import { PlansView } from './views/Plans';
import { usePlannerStore } from './store/plannerStore';
import { useSSEBridge } from './lib/queries';
import { absTime } from './lib/time';
import './styles/global.css';
import styles from './App.module.css';

/**
 * While the stream is not live, the main view is marked stale instead of
 * silently showing old data: dimmed slightly, with a "data as of" notice.
 */
function StaleNotice() {
  const { state, lastEventAt } = usePlannerStore((s) => s.connection);
  if (state === 'live' || state === 'connecting') return null;
  return (
    <div className={styles.staleNotice} role="status">
      Live stream {state === 'down' ? 'disconnected' : 'reconnecting'} — showing data as of{' '}
      {lastEventAt ? absTime(lastEventAt) : 'initial load'}.
    </div>
  );
}

/** One plan's shell: rail + view + chat + gate, all scoped by the route param. */
function PlanShell() {
  const { planId = '' } = useParams();
  const connState = usePlannerStore((s) => s.connection.state);

  return (
    <div className={styles.body}>
      <LifecycleRail />
      <main className={`${styles.main} ${connState === 'down' || connState === 'reconnecting' ? styles.stale : ''}`}>
        <StaleNotice />
        <div className={styles.viewScroll}>
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/goals" element={<GoalsView />} />
            <Route path="/agents" element={<AgentsView />} />
            <Route path="/activity" element={<ActivityView />} />
            <Route path="*" element={<Overview />} />
          </Routes>
        </div>
        <ConsoleDock />
      </main>
      <ChatPanel />
      <GatePanel planId={planId} />
    </div>
  );
}

export default function App() {
  useSSEBridge();

  return (
    <BrowserRouter>
      <div className={styles.shell}>
        <TopBar />
        <Routes>
          <Route
            path="/"
            element={
              <div className={styles.body}>
                <main className={styles.main}>
                  <div className={styles.viewScroll}>
                    <PlansView />
                  </div>
                </main>
              </div>
            }
          />
          <Route
            path="/settings"
            element={
              <div className={styles.body}>
                <main className={styles.main}>
                  <div className={styles.viewScroll}>
                    <SettingsView />
                  </div>
                </main>
              </div>
            }
          />
          <Route path="/plans/:planId/*" element={<PlanShell />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        <Toaster />
      </div>
    </BrowserRouter>
  );
}
