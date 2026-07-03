import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ChevronRight, Plus } from 'lucide-react';
import { useCreatePlan, usePlans } from '../lib/queries';
import { StatusBadge } from '../components/StatusBadge';
import { tokens } from '../styles/tokens';
import styles from './Overview.module.css';

/**
 * The entry point: every plan in the orchestrator, newest activity first,
 * plus the "new plan" composer. Creating a plan lands you in its shell with
 * the discovery chat open.
 */
export function PlansView() {
  const { data: plans = [], isLoading, error, refetch } = usePlans();
  const createPlan = useCreatePlan();
  const navigate = useNavigate();

  const [composing, setComposing] = useState(false);
  const [brief, setBrief] = useState('');

  const submit = () => {
    if (!brief.trim() || createPlan.isPending) return;
    createPlan.mutate(brief, {
      onSuccess: ({ plan_id }) => {
        setComposing(false);
        setBrief('');
        navigate(`/plans/${encodeURIComponent(plan_id)}`);
      },
    });
  };

  if (error) {
    return (
      <div className={styles.page}>
        <div className={styles.errorCard} role="alert">
          <div className={styles.errorTitle}>Can't reach the backend</div>
          <p className={styles.errorBody}>
            {(error as Error).message}. Check that the API server is running at{' '}
            <code>{import.meta.env.VITE_API_URL ?? 'http://localhost:8000'}</code>, then retry.
          </p>
          <button className={styles.retryBtn} onClick={() => refetch()}>Retry</button>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <header className={styles.phaseHeader}>
        <div className={styles.phaseTitleRow}>
          <h1 className={styles.phaseTitle}>Plans</h1>
          <button
            onClick={() => setComposing((v) => !v)}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 6,
              padding: '6px 12px', borderRadius: tokens.r6, cursor: 'pointer',
              background: tokens.accent, border: 'none', color: '#fff',
              fontFamily: tokens.fontSans, fontSize: 12, fontWeight: 600,
            }}
          >
            <Plus size={14} aria-hidden /> New plan
          </button>
        </div>
        <p className={styles.phaseGoal}>
          Each plan walks the 9-phase machine: chat the roadmap, approve it,
          watch execution, review, replan.
        </p>
      </header>

      {composing && (
        <section className={styles.section} aria-label="New plan">
          <h2 className={styles.sectionTitle + ' label'}>New plan — the brief</h2>
          <textarea
            value={brief}
            onChange={(e) => setBrief(e.target.value)}
            placeholder="Describe what you want built. The discovery conversation starts from this brief."
            rows={4}
            autoFocus
            style={{
              width: '100%', boxSizing: 'border-box',
              background: tokens.inputBg, border: `1px solid ${tokens.border}`,
              borderRadius: tokens.r8, padding: '10px 12px',
              fontFamily: tokens.fontSans, fontSize: 12, color: tokens.textPrimary,
              outline: 'none', resize: 'vertical', lineHeight: 1.6,
            }}
          />
          <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
            <button
              onClick={submit}
              disabled={!brief.trim() || createPlan.isPending}
              style={{
                padding: '7px 14px', borderRadius: tokens.r6, cursor: 'pointer',
                background: tokens.accent, border: 'none', color: '#fff',
                fontFamily: tokens.fontSans, fontSize: 12, fontWeight: 600,
                opacity: !brief.trim() || createPlan.isPending ? 0.55 : 1,
              }}
            >
              {createPlan.isPending ? 'Creating…' : 'Create & start discovery'}
            </button>
            <button
              onClick={() => setComposing(false)}
              style={{
                padding: '7px 14px', borderRadius: tokens.r6, cursor: 'pointer',
                background: 'transparent', border: `1px solid ${tokens.border}`,
                color: tokens.textSecond, fontFamily: tokens.fontSans, fontSize: 12,
              }}
            >
              Cancel
            </button>
          </div>
        </section>
      )}

      <section className={styles.section} aria-label="All plans">
        <h2 className={styles.sectionTitle + ' label'}>All plans</h2>
        {isLoading ? (
          <div aria-busy="true">
            {[0, 1, 2].map((i) => (
              <div key={i} className="skeleton" style={{ height: 40, marginBottom: 6 }} />
            ))}
          </div>
        ) : plans.length === 0 ? (
          <p className={styles.empty}>No plans yet — create one to begin.</p>
        ) : (
          <ul className={styles.rows}>
            {plans.map((p) => (
              <li key={p.id}>
                <Link className={styles.row} to={`/plans/${encodeURIComponent(p.id)}`}>
                  <StatusBadge domain="phase" value={p.phase} bare />
                  <span className={styles.rowTitle} style={{ fontFamily: tokens.fontMono }}>
                    {p.id}
                  </span>
                  <span className={styles.rowMeta}>
                    iter {p.iteration} · v{p.version}
                    {p.claimed_by && ` · claimed by ${p.claimed_by}`}
                  </span>
                  <ChevronRight size={14} className={styles.rowChev} aria-hidden />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
