import React from 'react';
import { ReactFlowProvider } from '@xyflow/react';
import { Toolbar } from './components/Toolbar';
import { PlanCanvas } from './components/PlanCanvas';
import { ChatPanel } from './components/ChatPanel';
import { DetailPanel } from './components/DetailPanel';
import { AddNodeModal } from './components/AddNodeModal';
import { usePlannerStore } from './store/plannerStore';
import './styles/global.css';

export default function App() {
  const detailPanelOpen = usePlannerStore((s) => s.ui.detailPanelOpen);

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

        {/* Modal layer */}
        <AddNodeModal />
      </div>
    </ReactFlowProvider>
  );
}
