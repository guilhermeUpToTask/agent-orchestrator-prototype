import React from 'react';
import {
  Plus, RotateCcw, LayoutTemplate, CheckCircle,
  AlignHorizontalDistributeCenter, AlignVerticalDistributeCenter,
  ChevronLeft, Loader2,
} from 'lucide-react';
import { tokens, PHASE_STATUS_META } from '../styles/tokens';
import { usePlannerStore } from '../store/plannerStore';
import { useQueryClient } from '@tanstack/react-query';
import {
  useApproveArchitecture,
  useApproveBrief,
  useApprovePhase,
  useGoals,
  usePlan,
} from '../lib/queries';

function StatPill({ value, label, color }: { value: number; label: string; color: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
      <span style={{ fontSize: 16, fontWeight: 700, color, fontFamily: tokens.fontMono, lineHeight: 1 }}>{value}</span>
      <span style={{ fontSize: 8, color: tokens.textMuted, fontFamily: tokens.fontMono, letterSpacing: '0.08em' }}>{label}</span>
    </div>
  );
}

function Btn({
  icon, label, onClick, color, bg, danger, disabled, pulse,
}: {
  icon: React.ReactNode; label: string; onClick?: () => void;
  color?: string; bg?: string; danger?: boolean; disabled?: boolean; pulse?: boolean;
}) {
  const c = danger ? tokens.red : (color ?? tokens.textSecond);
  const b = danger ? tokens.redDim + '44' : (bg ?? 'transparent');
  return (
    <button onClick={onClick} disabled={disabled} title={label} style={{
      display: 'flex', alignItems: 'center', gap: 5,
      padding: '5px 10px', background: b,
      border: `1px solid ${danger ? tokens.red + '33' : c + '33'}`,
      borderRadius: tokens.r6,
      color: disabled ? tokens.textMuted : c,
      cursor: disabled ? 'default' : 'pointer',
      fontFamily: tokens.fontMono, fontSize: 9,
      letterSpacing: '0.06em', whiteSpace: 'nowrap',
      opacity: disabled ? 0.5 : 1,
      animation: pulse ? 'glow 2s ease-in-out infinite' : 'none',
      ['--glow-color' as string]: c,
    }}>
      {icon}<span>{label}</span>
    </button>
  );
}

function Divider() {
  return <div style={{ width: 1, height: 20, background: tokens.border, margin: '0 2px' }} />;
}

export function Toolbar() {
  const ui = usePlannerStore((s) => s.ui);
  const setLayoutDirection = usePlannerStore((s) => s.setLayoutDirection);
  const toggleChatPanel = usePlannerStore((s) => s.toggleChatPanel);

  const queryClient = useQueryClient();
  const { data: plan } = usePlan();
  const { data: goals, isSuccess: loaded } = useGoals();

  const approveBrief = useApproveBrief();
  const approveArchitecture = useApproveArchitecture();
  const approvePhase = useApprovePhase();

  const doApproveBrief = () => approveBrief.mutate();
  const doApproveArchitecture = (ids: string[]) => approveArchitecture.mutate(ids);
  const doApprovePhase = (next: boolean) => approvePhase.mutate(next);
  const refresh = () => queryClient.invalidateQueries();
  // Layout is derived from layoutDirection — toggling re-runs the dagre pass
  const autoLayout = () => setLayoutDirection(ui.layoutDirection);

  const planStatus = plan?.status ?? 'discovery';
  const phaseMeta = PHASE_STATUS_META[planStatus] ?? PHASE_STATUS_META.discovery;
  const currentPhase = plan?.phases[plan.current_phase_index ?? 0];

  const taskStatuses = (goals ?? []).flatMap((g) => g.tasks.map((t) => t.status));
  const stats = {
    done: taskStatuses.filter((s) => ['succeeded', 'merged'].includes(s)).length,
    running: taskStatuses.filter((s) => ['in_progress', 'assigned'].includes(s)).length,
    pending: taskStatuses.filter((s) => s === 'created').length,
    failed: taskStatuses.filter((s) => ['failed', 'canceled'].includes(s)).length,
  };

  return (
    <div style={{
      height: 52, background: tokens.panelBg,
      borderBottom: `1px solid ${tokens.border}`,
      display: 'flex', alignItems: 'center',
      padding: '0 14px', gap: 8, flexShrink: 0,
      overflowX: 'auto',
    }}>
      {/* Brand */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
        <div style={{
          width: 28, height: 28, borderRadius: tokens.r6,
          background: `linear-gradient(135deg, ${tokens.accent}, ${tokens.purple})`,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          boxShadow: `0 0 12px ${tokens.accentGlow}`,
        }}>
          <span style={{ fontSize: 13, fontFamily: tokens.fontMono, fontWeight: 700, color: '#fff' }}>A</span>
        </div>
        <div>
          <div style={{ fontFamily: tokens.fontMono, fontSize: 12, color: tokens.textPrimary, letterSpacing: '0.06em', lineHeight: 1 }}>AIPOM</div>
          <div style={{ fontFamily: tokens.fontMono, fontSize: 8, color: tokens.textMuted, letterSpacing: '0.08em' }}>PLANNING LAYER</div>
        </div>
      </div>

      <Divider />

      {/* Phase badge */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 6,
        padding: '4px 10px',
        background: phaseMeta.color + '11',
        border: `1px solid ${phaseMeta.color + '33'}`,
        borderRadius: tokens.r6, flexShrink: 0,
      }}>
        <div style={{ width: 6, height: 6, borderRadius: '50%', background: phaseMeta.color, boxShadow: `0 0 6px ${phaseMeta.color}` }} />
        <span style={{ fontSize: 9, fontFamily: tokens.fontMono, color: phaseMeta.color, letterSpacing: '0.08em' }}>
          {phaseMeta.label}
        </span>
        {currentPhase && (
          <span style={{ fontSize: 9, color: tokens.textMuted, fontFamily: tokens.fontMono }}>
            — {currentPhase.name}
          </span>
        )}
      </div>

      <Divider />

      {/* Stats */}
      {loaded && (
        <div style={{ display: 'flex', gap: 14, flexShrink: 0 }}>
          <StatPill value={stats.done} label="DONE" color={tokens.green} />
          <StatPill value={stats.running} label="RUNNING" color={tokens.yellow} />
          <StatPill value={stats.pending} label="PENDING" color={tokens.textMuted} />
          {stats.failed > 0 && <StatPill value={stats.failed} label="FAILED" color={tokens.red} />}
        </div>
      )}

      <div style={{ flex: 1 }} />

      {/* ── M2: Conditional approval buttons ─────────────────────────────── */}
      {planStatus === 'discovery' && (
        <Btn
          icon={<CheckCircle size={12} />}
          label="APPROVE BRIEF"
          onClick={doApproveBrief}
          color={tokens.green}
          bg={tokens.greenDim + '44'}
          disabled={ui.isThinking}
          pulse
        />
      )}

      {planStatus === 'architecture' && (
        <Btn
          icon={<CheckCircle size={12} />}
          label="APPROVE ARCHITECTURE"
          onClick={() => doApproveArchitecture([])} // empty = approve all decisions
          color={tokens.cyan}
          bg={tokens.cyanDim + '44'}
          disabled={ui.isThinking}
          pulse
        />
      )}

      {planStatus === 'phase_review' && (
        <>
          <Btn
            icon={<CheckCircle size={12} />}
            label="APPROVE PHASE →"
            onClick={() => doApprovePhase(true)}
            color={tokens.green}
            bg={tokens.greenDim + '44'}
            disabled={ui.isThinking}
            pulse
          />
          <Btn
            icon={<CheckCircle size={12} />}
            label="MARK DONE"
            onClick={() => doApprovePhase(false)}
            color={tokens.textMuted}
            disabled={ui.isThinking}
          />
        </>
      )}

      <Divider />

      {/* Layout controls */}
      <Btn
        icon={<AlignHorizontalDistributeCenter size={12} />}
        label="LR"
        onClick={() => setLayoutDirection('LR')}
        color={ui.layoutDirection === 'LR' ? tokens.accent : tokens.textMuted}
        bg={ui.layoutDirection === 'LR' ? tokens.accentDim + '33' : 'transparent'}
      />
      <Btn
        icon={<AlignVerticalDistributeCenter size={12} />}
        label="TB"
        onClick={() => setLayoutDirection('TB')}
        color={ui.layoutDirection === 'TB' ? tokens.accent : tokens.textMuted}
        bg={ui.layoutDirection === 'TB' ? tokens.accentDim + '33' : 'transparent'}
      />
      <Btn icon={<LayoutTemplate size={12} />} label="AUTO" onClick={autoLayout} color={tokens.textSecond} />

      <Divider />

      {/* Refresh */}
      <Btn
        icon={ui.isThinking ? <Loader2 size={12} style={{ animation: 'spin 1s linear infinite' }} /> : <RotateCcw size={12} />}
        label="REFRESH"
        onClick={refresh}
        color={tokens.textSecond}
        disabled={ui.isThinking}
      />

      {/* Chat toggle */}
      <Btn
        icon={<ChevronLeft size={12} />}
        label={ui.chatPanelCollapsed ? 'CHAT' : 'HIDE'}
        onClick={toggleChatPanel}
        color={tokens.textSecond}
      />
    </div>
  );
}
