import React, { useEffect, useRef } from 'react';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { TopBar } from './components/TopBar';
import { LifecycleRail } from './components/LifecycleRail';
import { GatePanel } from './components/GatePanel';
import { ChatPanel } from './components/ChatPanel';
import { ConsoleDock } from './components/ConsoleDock';
import { Toaster } from './components/Toaster';
import { Overview } from './views/Overview';
import { GoalsView, GoalDetail } from './views/Goals';
import { ActivityView } from './views/Activity';
import { AgentsView } from './views/Agents';
import { SettingsView } from './views/Settings';
import { PullRequestsView } from './views/PullRequests';
import { usePlannerStore, ts } from './store/plannerStore';
import { useArchitectureStatusSync, usePlan, useSSEBridge } from './lib/queries';
import { absTime } from './lib/time';
import './styles/global.css';
import styles from './App.module.css';

/** One intro line when the backend first answers — history lives in Activity. */
function useChatHydration() {
  const addMessage = usePlannerStore((s) => s.addMessage);
  const { data: plan } = usePlan();
  const hydrated = useRef(false);

  useEffect(() => {
    if (hydrated.current || !plan) return;
    hydrated.current = true;
    addMessage({
      role: 'assistant',
      text:
        plan.status === 'phase_active'
          ? 'Connected. Chat is wired to the tactical planner — type a refinement request.'
          : plan.status === 'discovery'
            ? 'Connected. Start a discovery session from the lifecycle rail on the left — then answer the planner’s questions here to build the project brief.'
            : 'Connected. Approvals live in the gate card on the left rail.',
      ts: ts(),
    });
  }, [plan, addMessage]);
}

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

export default function App() {
  const connState = usePlannerStore((s) => s.connection.state);

  useSSEBridge();
  useArchitectureStatusSync();
  useChatHydration();

  return (
    <BrowserRouter>
      <div className={styles.shell}>
        <TopBar />
        <div className={styles.body}>
          <LifecycleRail />
          <main className={`${styles.main} ${connState === 'down' || connState === 'reconnecting' ? styles.stale : ''}`}>
            <StaleNotice />
            <div className={styles.viewScroll}>
              <Routes>
                <Route path="/" element={<Overview />} />
                <Route path="/goals" element={<GoalsView />} />
                <Route path="/goals/:goalId" element={<GoalDetail />} />
                <Route path="/agents" element={<AgentsView />} />
                <Route path="/settings" element={<SettingsView />} />
                <Route path="/prs" element={<PullRequestsView />} />
                <Route path="/activity" element={<ActivityView />} />
                <Route path="*" element={<Overview />} />
              </Routes>
            </div>
            <ConsoleDock />
          </main>
          <ChatPanel />
        </div>
        <GatePanel />
        <Toaster />
      </div>
    </BrowserRouter>
  );
}
