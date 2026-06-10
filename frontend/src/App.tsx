import React, { useEffect, useRef } from 'react';
import { ReactFlowProvider } from '@xyflow/react';
import { Toolbar } from './components/Toolbar';
import { PlanCanvas } from './components/PlanCanvas';
import { ChatPanel } from './components/ChatPanel';
import { DetailPanel } from './components/DetailPanel';
import { usePlannerStore, ts } from './store/plannerStore';
import { useGoals, usePlan, usePlanHistory, useSSEBridge } from './lib/queries';
import './styles/global.css';

/**
 * Hydrate the chat transcript once when the backend data first arrives:
 * recent plan history as system entries plus a connection intro.
 */
function useChatHydration() {
  const addMessage = usePlannerStore((s) => s.addMessage);
  const { data: plan } = usePlan();
  const { data: goals } = useGoals();
  const { data: history } = usePlanHistory();
  const hydrated = useRef(false);

  useEffect(() => {
    if (hydrated.current || !plan || !goals || !history) return;
    hydrated.current = true;

    for (const h of history.slice(-20)) {
      addMessage({
        role: 'system',
        text: `[${h.actor ?? 'system'}] ${h.event}${h.detail ? ' — ' + JSON.stringify(h.detail) : ''}`,
        ts: h.timestamp
          ? new Date(h.timestamp).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
          : ts(),
      });
    }

    addMessage({
      role: 'assistant',
      text: `AIPOM connected. Plan status: ${plan.status} · ${goals.length} goals · ${goals.reduce((n, g) => n + g.tasks.length, 0)} tasks. ${
        plan.status === 'phase_active'
          ? 'Chat is wired to the planning engine — type a refinement request.'
          : plan.status === 'discovery'
            ? 'Discovery is active. Answer questions to build the project brief.'
            : 'Use the approval buttons in the toolbar to advance the plan.'
      }`,
      ts: ts(),
    });
  }, [plan, goals, history, addMessage]);
}

export default function App() {
  const detailPanelOpen = usePlannerStore((s) => s.ui.detailPanelOpen);

  useSSEBridge();
  useChatHydration();

  return (
    <ReactFlowProvider>
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100vh',
        width: '100vw',
        overflow: 'hidden',
        background: '#0b0d12',
      }}>
        {/* Toolbar */}
        <Toolbar />

        {/* Main content row */}
        <div style={{
          flex: 1,
          display: 'flex',
          overflow: 'hidden',
          position: 'relative',
        }}>
          {/* Plan canvas — fills available space */}
          <PlanCanvas />

          {/* Detail panel — overlays canvas on the right */}
          {detailPanelOpen && <DetailPanel />}

          {/* Chat panel — fixed right column */}
          <ChatPanel />
        </div>
      </div>
    </ReactFlowProvider>
  );
}
