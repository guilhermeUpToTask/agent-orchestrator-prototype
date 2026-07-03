import React, { useState } from 'react';
import { Settings as SettingsIcon, Check } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { tokens } from '../styles/tokens';
import {
  getConfigScope,
  listCapabilities,
  listModels,
  listProviders,
  setConfigKey,
} from '../lib/api';
import { useAgents } from '../lib/queries';
import { toast, errorDetail } from '../lib/toast';

const SCOPE = 'orchestrator';

const card: React.CSSProperties = {
  background: tokens.cardBg,
  border: `1px solid ${tokens.border}`,
  borderRadius: tokens.r12,
  padding: '14px 16px',
};

const h = (text: string) => (
  <div style={{
    fontSize: 9, fontFamily: tokens.fontMono, color: tokens.textMuted,
    letterSpacing: '0.1em', marginBottom: 10, textTransform: 'uppercase',
  }}>{text}</div>
);

const mono: React.CSSProperties = {
  fontSize: 10, fontFamily: tokens.fontMono, color: tokens.textSecond, lineHeight: 1.8,
};

/**
 * Machine settings: the reasoner config keys (two-tier config, scope
 * 'orchestrator'), the providers/models catalog and the capability +
 * agent registries — the read models behind `orchestrate seed demo`.
 */
export function SettingsView() {
  const qc = useQueryClient();
  const { data: config = {} } = useQuery({
    queryKey: ['config', SCOPE],
    queryFn: () => getConfigScope(SCOPE),
  });
  const { data: providers = [] } = useQuery({
    queryKey: ['providers'],
    queryFn: listProviders,
  });
  const { data: models = [] } = useQuery({ queryKey: ['models'], queryFn: listModels });
  const { data: capabilities = [] } = useQuery({
    queryKey: ['capabilities'],
    queryFn: listCapabilities,
  });
  const { data: agents = [] } = useAgents();

  const [draft, setDraft] = useState<Record<string, string>>({});
  const save = useMutation({
    mutationFn: ({ key, value }: { key: string; value: string }) =>
      setConfigKey(SCOPE, key, value),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['config', SCOPE] });
      toast.success('Config saved', 'Restart the API/worker to apply reasoner changes.');
    },
    onError: (err) => toast.error('Config save failed', errorDetail(err)),
  });

  const REASONER_KEYS = [
    { key: 'reasoner.mode', hint: 'stub | llm' },
    { key: 'reasoner.provider_id', hint: 'providers.id' },
    { key: 'reasoner.model_id', hint: 'models.id' },
    { key: 'reasoner.temperature', hint: 'default 0.2' },
    { key: 'reasoner.max_turns', hint: 'default 8' },
  ];

  return (
    <div style={{ padding: 18, overflowY: 'auto', height: '100%', display: 'flex', flexDirection: 'column', gap: 14, maxWidth: 760 }}>
      <h2 style={{
        fontSize: 13, fontFamily: tokens.fontMono, color: tokens.textPrimary,
        letterSpacing: '0.06em', margin: 0, display: 'flex', alignItems: 'center', gap: 8,
      }}>
        <SettingsIcon size={15} aria-hidden /> SETTINGS
      </h2>

      <div style={card}>
        {h('Reasoner (scope: orchestrator)')}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {REASONER_KEYS.map(({ key, hint }) => {
            const current = config[key] ?? '';
            const value = draft[key] ?? current;
            const dirty = value !== current;
            return (
              <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ ...mono, width: 200, flexShrink: 0 }}>{key}</span>
                <input
                  value={value}
                  placeholder={hint}
                  onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
                  style={{
                    flex: 1, background: tokens.inputBg,
                    border: `1px solid ${dirty ? tokens.accent : tokens.border}`,
                    borderRadius: tokens.r6, padding: '5px 8px',
                    fontFamily: tokens.fontMono, fontSize: 11, color: tokens.textPrimary,
                    outline: 'none',
                  }}
                />
                <button
                  onClick={() => save.mutate({ key, value })}
                  disabled={!dirty || save.isPending}
                  title="Save"
                  style={{
                    display: 'flex', alignItems: 'center', padding: '5px 8px',
                    borderRadius: tokens.r6, cursor: dirty ? 'pointer' : 'default',
                    background: dirty ? tokens.accent : '#1a1d2a', border: 'none',
                    color: dirty ? '#fff' : tokens.textMuted,
                  }}
                >
                  <Check size={12} aria-hidden />
                </button>
              </div>
            );
          })}
        </div>
        <p style={{ ...mono, color: tokens.textMuted, marginTop: 10, marginBottom: 0 }}>
          Provider keys are stored envelope-encrypted — seed them with{' '}
          <code>orchestrate seed demo --provider … --api-key-env …</code>. They are
          never readable through the API.
        </p>
      </div>

      <div style={card}>
        {h('Providers & models')}
        {providers.length === 0 ? (
          <span style={mono}>none — <code>orchestrate seed demo</code> registers one</span>
        ) : (
          providers.map((p) => (
            <div key={p.id} style={mono}>
              <strong style={{ color: tokens.textPrimary }}>{p.id}</strong> · {p.base_url} ·{' '}
              key {p.api_key_ref}
              {models.filter((m) => m.provider_id === p.id).map((m) => (
                <div key={m.id} style={{ paddingLeft: 16 }}>
                  ↳ {m.id} ({m.name})
                </div>
              ))}
            </div>
          ))
        )}
      </div>

      <div style={card}>
        {h('Capabilities')}
        {capabilities.length === 0 ? (
          <span style={mono}>none</span>
        ) : (
          capabilities.map((c) => (
            <div key={c.id} style={mono}>
              <strong style={{ color: tokens.textPrimary }}>{c.id}</strong> — {c.name}
              {c.description ? ` · ${c.description}` : ''}
            </div>
          ))
        )}
      </div>

      <div style={card}>
        {h('Agents')}
        {agents.length === 0 ? (
          <span style={mono}>none</span>
        ) : (
          agents.map((a) => (
            <div key={a.id} style={mono}>
              <strong style={{ color: tokens.textPrimary }}>{a.id}</strong> · {a.role} ·{' '}
              caps: {(a.capabilities ?? []).map((c) => c.id).join(', ') || '—'}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
