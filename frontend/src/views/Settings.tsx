import React, { useState } from 'react';
import { Settings as SettingsIcon, Trash2, Plus, Check } from 'lucide-react';

import { tokens } from '../styles/tokens';
import {
  useAddModel,
  useAgentDefinitions,
  useCreateProject,
  useDeleteAgentDefinition,
  useDeleteProject,
  useDeleteProvider,
  useProjects,
  useProviders,
  useRegisterAgentDefinition,
  useRegisterProvider,
  useSecretRefs,
} from '../lib/controlQueries';
import { useProjectStore } from '../store/projectStore';
import type { ProviderKind } from '../types/control';

type Tab = 'projects' | 'providers' | 'agents' | 'secrets';

const TABS: { id: Tab; label: string }[] = [
  { id: 'projects', label: 'Projects' },
  { id: 'providers', label: 'Providers & Models' },
  { id: 'agents', label: 'Agents' },
  { id: 'secrets', label: 'Secrets' },
];

// ─── Shared styles ────────────────────────────────────────────────────────────

const card: React.CSSProperties = {
  background: tokens.cardBg,
  border: `1px solid ${tokens.border}`,
  borderRadius: tokens.r12,
  padding: '12px 14px',
  marginBottom: 10,
};
const label: React.CSSProperties = {
  fontSize: 10, fontFamily: tokens.fontMono, color: tokens.textMuted,
  textTransform: 'uppercase', letterSpacing: '0.06em', display: 'block', marginBottom: 4,
};
const input: React.CSSProperties = {
  width: '100%', background: tokens.inputBg, border: `1px solid ${tokens.border}`,
  borderRadius: tokens.r8, color: tokens.textPrimary, padding: '6px 8px',
  fontSize: 12, fontFamily: tokens.fontMono, marginBottom: 8,
};
const btn: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 6, background: tokens.accentDim,
  border: `1px solid ${tokens.accent}`, color: tokens.accent, borderRadius: tokens.r8,
  padding: '6px 10px', fontSize: 12, fontFamily: tokens.fontMono, cursor: 'pointer',
};
const iconBtn: React.CSSProperties = {
  background: 'transparent', border: 'none', color: tokens.textMuted, cursor: 'pointer',
  display: 'inline-flex', alignItems: 'center',
};

function Field(props: {
  label: string; value: string; onChange: (v: string) => void;
  placeholder?: string; type?: string;
}) {
  return (
    <div>
      <label style={label}>{props.label}</label>
      <input
        style={input}
        type={props.type ?? 'text'}
        value={props.value}
        placeholder={props.placeholder}
        onChange={(e) => props.onChange(e.target.value)}
      />
    </div>
  );
}

// ─── Projects tab ───────────────────────────────────────────────────────────────

function ProjectsTab() {
  const { data: projects = [] } = useProjects();
  const create = useCreateProject();
  const remove = useDeleteProject();
  const activeId = useProjectStore((s) => s.activeProjectId);

  const [name, setName] = useState('');
  const [repoUrl, setRepoUrl] = useState('');
  const [branch, setBranch] = useState('main');
  const [token, setToken] = useState('');

  const submit = () => {
    create.mutate(
      { name, repo_url: repoUrl, default_branch: branch, github_token: token || null },
      { onSuccess: () => { setName(''); setRepoUrl(''); setBranch('main'); setToken(''); } },
    );
  };

  return (
    <div>
      {projects.map((p) => (
        <div key={p.id} style={{ ...card, display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 13, color: tokens.textPrimary, fontWeight: 600 }}>
              {p.name} {p.id === activeId && <Check size={12} color={tokens.green} />}
            </div>
            <div style={{ fontSize: 11, color: tokens.textMuted, fontFamily: tokens.fontMono }}>
              {p.repo_url} · {p.default_branch} · github {p.has_github_token ? '•••• set' : 'not set'}
            </div>
          </div>
          <button
            style={iconBtn}
            title="Delete project"
            onClick={() => remove.mutate({ id: p.id })}
          >
            <Trash2 size={14} />
          </button>
        </div>
      ))}

      <div style={card}>
        <Field label="Name" value={name} onChange={setName} placeholder="My App" />
        <Field label="Repo URL" value={repoUrl} onChange={setRepoUrl} placeholder="git@github.com:me/app.git" />
        <Field label="Default branch" value={branch} onChange={setBranch} />
        <Field label="GitHub token (write-only)" value={token} onChange={setToken} type="password" placeholder="ghp_…" />
        <button style={btn} onClick={submit} disabled={create.isPending || !name || !repoUrl}>
          <Plus size={13} /> Create project
        </button>
      </div>
    </div>
  );
}

// ─── Providers tab ──────────────────────────────────────────────────────────────

const KINDS: ProviderKind[] = ['anthropic', 'gemini', 'openrouter', 'openai'];

function ProvidersTab() {
  const { data: providers = [] } = useProviders();
  const register = useRegisterProvider();
  const remove = useDeleteProvider();
  const addModel = useAddModel();

  const [id, setId] = useState('');
  const [kind, setKind] = useState<ProviderKind>('anthropic');
  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [modelInputs, setModelInputs] = useState<Record<string, string>>({});

  return (
    <div>
      {providers.map((p) => (
        <div key={p.id} style={card}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ flex: 1, fontSize: 13, color: tokens.textPrimary, fontWeight: 600 }}>
              {p.id} <span style={{ fontSize: 11, color: tokens.textMuted }}>[{p.kind}]</span>
            </div>
            <button style={iconBtn} title="Delete provider" onClick={() => remove.mutate(p.id)}>
              <Trash2 size={14} />
            </button>
          </div>
          <div style={{ fontSize: 11, color: tokens.textMuted, fontFamily: tokens.fontMono, margin: '4px 0' }}>
            models: {p.models.length ? p.models.map((m) => m.model_id).join(', ') : '(none)'}
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <input
              style={{ ...input, marginBottom: 0 }}
              placeholder="add model id"
              value={modelInputs[p.id] ?? ''}
              onChange={(e) => setModelInputs((s) => ({ ...s, [p.id]: e.target.value }))}
            />
            <button
              style={btn}
              onClick={() => {
                const mid = modelInputs[p.id];
                if (mid) addModel.mutate(
                  { providerId: p.id, body: { model_id: mid } },
                  { onSuccess: () => setModelInputs((s) => ({ ...s, [p.id]: '' })) },
                );
              }}
            >
              <Plus size={13} />
            </button>
          </div>
        </div>
      ))}

      <div style={card}>
        <Field label="Provider id" value={id} onChange={setId} placeholder="anthropic" />
        <label style={label}>Kind</label>
        <select
          style={{ ...input, appearance: 'auto' as React.CSSProperties['appearance'] }}
          value={kind}
          onChange={(e) => setKind(e.target.value as ProviderKind)}
        >
          {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
        </select>
        <Field label="API key (write-only)" value={apiKey} onChange={setApiKey} type="password" placeholder="sk-…" />
        <Field label="Base URL (optional)" value={baseUrl} onChange={setBaseUrl} placeholder="https://…" />
        <button
          style={btn}
          disabled={register.isPending || !id || !apiKey}
          onClick={() => register.mutate(
            { id, kind, api_key: apiKey, base_url: baseUrl || null },
            { onSuccess: () => { setId(''); setApiKey(''); setBaseUrl(''); } },
          )}
        >
          <Plus size={13} /> Register provider
        </button>
      </div>
    </div>
  );
}

// ─── Agents tab ─────────────────────────────────────────────────────────────────

function AgentsTab() {
  const { data: agents = [] } = useAgentDefinitions();
  const { data: providers = [] } = useProviders();
  const register = useRegisterAgentDefinition();
  const remove = useDeleteAgentDefinition();

  const [id, setId] = useState('');
  const [name, setName] = useState('');
  const [runtime, setRuntime] = useState('claude');
  const [providerId, setProviderId] = useState('');
  const [modelId, setModelId] = useState('');
  const [caps, setCaps] = useState('');

  const provider = providers.find((p) => p.id === providerId);

  return (
    <div>
      {agents.map((a) => (
        <div key={a.id} style={{ ...card, display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 13, color: tokens.textPrimary, fontWeight: 600 }}>{a.name}</div>
            <div style={{ fontSize: 11, color: tokens.textMuted, fontFamily: tokens.fontMono }}>
              {a.runtime_type} · {a.provider_id}/{a.model_id} · {a.capabilities.join(', ') || '—'}
            </div>
          </div>
          <button style={iconBtn} title="Delete agent" onClick={() => remove.mutate(a.id)}>
            <Trash2 size={14} />
          </button>
        </div>
      ))}

      <div style={card}>
        <Field label="Agent id" value={id} onChange={setId} placeholder="worker-1" />
        <Field label="Name" value={name} onChange={setName} placeholder="Worker" />
        <label style={label}>Runtime</label>
        <select style={{ ...input, appearance: 'auto' as React.CSSProperties['appearance'] }} value={runtime} onChange={(e) => setRuntime(e.target.value)}>
          {['claude', 'gemini', 'pi'].map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
        <label style={label}>Provider</label>
        <select style={{ ...input, appearance: 'auto' as React.CSSProperties['appearance'] }} value={providerId} onChange={(e) => { setProviderId(e.target.value); setModelId(''); }}>
          <option value="">— select —</option>
          {providers.map((p) => <option key={p.id} value={p.id}>{p.id}</option>)}
        </select>
        <label style={label}>Model</label>
        <select style={{ ...input, appearance: 'auto' as React.CSSProperties['appearance'] }} value={modelId} onChange={(e) => setModelId(e.target.value)} disabled={!provider}>
          <option value="">— select —</option>
          {provider?.models.map((m) => <option key={m.model_id} value={m.model_id}>{m.model_id}</option>)}
        </select>
        <Field label="Capabilities (comma-separated)" value={caps} onChange={setCaps} placeholder="code:backend, test:write" />
        <button
          style={btn}
          disabled={register.isPending || !id || !providerId || !modelId}
          onClick={() => register.mutate(
            {
              id, name: name || id, runtime_type: runtime,
              provider_id: providerId, model_id: modelId,
              capabilities: caps.split(',').map((c) => c.trim()).filter(Boolean),
            },
            { onSuccess: () => { setId(''); setName(''); setCaps(''); } },
          )}
        >
          <Plus size={13} /> Register agent
        </button>
      </div>
    </div>
  );
}

// ─── Secrets tab ────────────────────────────────────────────────────────────────

function SecretsTab() {
  const { data: refs = [] } = useSecretRefs();
  return (
    <div>
      <p style={{ fontSize: 11, color: tokens.textMuted, fontFamily: tokens.fontMono, marginBottom: 10 }}>
        Secrets are write-only. Values are never shown — only whether each ref is set.
      </p>
      {refs.length === 0 && (
        <p style={{ fontSize: 12, color: tokens.textMuted }}>No secrets stored yet.</p>
      )}
      {refs.map((s) => (
        <div key={s.uri} style={{ ...card, display: 'flex', alignItems: 'center', gap: 10 }}>
          <code style={{ flex: 1, fontSize: 12, color: tokens.textPrimary }}>{s.uri}</code>
          <span style={{ fontSize: 11, color: s.is_set ? tokens.green : tokens.textMuted, fontFamily: tokens.fontMono }}>
            {s.is_set ? '•••• set' : 'not set'}
          </span>
        </div>
      ))}
    </div>
  );
}

// ─── Shell ──────────────────────────────────────────────────────────────────────

export function SettingsView() {
  const [tab, setTab] = useState<Tab>('projects');

  return (
    <div style={{ padding: 18, overflowY: 'auto', height: '100%', maxWidth: 720 }}>
      <h2 style={{
        fontSize: 13, fontFamily: tokens.fontMono, color: tokens.textPrimary,
        letterSpacing: '0.06em', marginBottom: 14, display: 'flex', alignItems: 'center', gap: 8,
      }}>
        <SettingsIcon size={15} aria-hidden /> SETTINGS
      </h2>

      <div style={{ display: 'flex', gap: 6, marginBottom: 14, flexWrap: 'wrap' }}>
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            style={{
              ...btn,
              background: tab === t.id ? tokens.accentDim : 'transparent',
              color: tab === t.id ? tokens.accent : tokens.textMuted,
              borderColor: tab === t.id ? tokens.accent : tokens.border,
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'projects' && <ProjectsTab />}
      {tab === 'providers' && <ProvidersTab />}
      {tab === 'agents' && <AgentsTab />}
      {tab === 'secrets' && <SecretsTab />}
    </div>
  );
}
