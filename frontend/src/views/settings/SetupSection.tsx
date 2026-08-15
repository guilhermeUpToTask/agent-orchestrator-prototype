import React from 'react';
import { Link } from 'react-router-dom';
import { CheckCircle2, Circle, Rocket } from 'lucide-react';
import {
  useAgents,
  useConfigScope,
  useCreateAgent,
  useCreateModel,
  useCreateProject,
  useCreateProvider,
  useDefaultAgent,
  useModels,
  useProjects,
  useProviders,
  useSetConfigKey,
  useSetDefaultAgent,
} from '../../lib/queries';
import { nextStep, setupSteps, type SetupFacts, type Tier } from '../../lib/setupPlan';
import { Button, Card, CountChip, Field, Input, Select } from '../../components/ui';
import styles from './Settings.module.css';

const SCOPE = 'orchestrator';

/**
 * The first-run path, in dependency order.
 *
 * `Readiness` answers whether the install is ready and links to the screen that
 * owns each failing check. It cannot answer what to do FIRST: one `catalog:
 * fail` covers providers, models and agents — three actions across two screens
 * with no progress until all of them land — and nothing warns that a reasoner
 * cannot be pointed at a model that does not exist yet.
 *
 * Every step here is also reachable from its own section; this screen only
 * sequences them and does the minimum write for each. Anything beyond the
 * minimum (capacity, retry policy, capabilities, model tiers) stays in the
 * expert panels, because a wizard that asks fifteen questions is one people
 * abandon at question four.
 */
export function SetupSection() {
  const providers = useProviders();
  const models = useModels();
  const agents = useAgents();
  const projects = useProjects();
  const defaultAgent = useDefaultAgent();
  const config = useConfigScope(SCOPE);

  const [tier, setTier] = React.useState<Tier>('tier0');

  const configValue = (key: string) => config.data?.[key] ?? '';

  const facts: SetupFacts = {
    tier,
    providers: providers.data?.length ?? 0,
    models: models.data?.length ?? 0,
    agents: agents.data?.length ?? 0,
    boundAgents: (agents.data ?? []).filter((agent) => agent.provider_id && agent.model_id).length,
    hasDefaultAgent: !!defaultAgent.data?.agent_id,
    projects: projects.data?.length ?? 0,
    reasonerMode: configValue('reasoner.mode') || 'stub',
    reasonerProviderId: configValue('reasoner.provider_id'),
    reasonerModelId: configValue('reasoner.model_id'),
    runnerMode: configValue('agent_runner.mode') || 'dry-run',
  };

  const steps = setupSteps(facts);
  const current = nextStep(facts);
  const complete = steps.filter((step) => step.done).length;

  return (
    <div className={styles.section}>
      <div className={styles.sectionHead}>
        <div>
          <h2 className={styles.sectionTitle}>Get started</h2>
          <p className={styles.sectionDesc}>
            Everything a new install needs, in the order the pieces depend on each other.
            Each step is also editable in its own section — this one just sequences them.
          </p>
        </div>
        <CountChip tone={current ? 'gate' : 'ok'}>
          {complete} / {steps.length}
        </CountChip>
      </div>

      <Card title="Which kind of run are you setting up?">
        <div className={styles.formGrid2}>
          <Field
            label="Tier"
            htmlFor="setup-tier"
            hint={
              tier === 'tier0'
                ? 'Stub planning + dry-run agents. Free, deterministic, no API key — the whole lifecycle without spending a token.'
                : 'Real models plan and real agent CLIs write code. Needs a provider key and the CLI on PATH.'
            }
          >
            <Select
              id="setup-tier"
              value={tier}
              onChange={(event) => setTier(event.target.value as Tier)}
              options={[
                { value: 'tier0', label: 'Tier 0 — free, no API key' },
                { value: 'tier1', label: 'Tier 1 — real models' },
              ]}
            />
          </Field>
        </div>
      </Card>

      <Card
        title={current ? `Next: ${current.title}` : 'Setup complete'}
        actions={current ? undefined : <CountChip tone="ok">ready</CountChip>}
      >
        {current ? (
          <StepForm stepId={current.id} tier={tier} facts={facts} />
        ) : (
          <div className={styles.readinessMain}>
            <span className={styles.itemName}>
              This install can run a {tier === 'tier0' ? 'Tier 0' : 'Tier 1'} cycle.
            </span>
            <span className={styles.itemMeta}>
              Open a plan from the plans screen and describe what you want built.
            </span>
            <Link className={styles.readinessLink} to="/">
              <Rocket size={13} aria-hidden /> Go to plans
            </Link>
          </div>
        )}
      </Card>

      <Card title="All steps">
        <div className={styles.readinessList}>
          {steps.map((step) => (
            <div className={styles.readinessRow} key={step.id}>
              {step.done ? (
                <CheckCircle2 size={15} aria-hidden className={styles.readiness_ok} />
              ) : (
                <Circle size={15} aria-hidden className={styles.readiness_warn} />
              )}
              <div className={styles.readinessMain}>
                <span className={styles.itemName}>{step.title}</span>
                <span className={styles.itemMeta}>{step.why}</span>
              </div>
              <Link className={styles.readinessLink} to={step.section}>
                Open section
              </Link>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

/** The minimum write for one step. Everything else lives in the expert panel. */
function StepForm({
  stepId,
  tier,
  facts,
}: {
  stepId: string;
  tier: Tier;
  facts: SetupFacts;
}) {
  switch (stepId) {
    case 'provider':
      return <ProviderStep />;
    case 'model':
      return <ModelStep />;
    case 'agent':
      return <AgentStep tier={tier} />;
    case 'default_agent':
      return <DefaultAgentStep />;
    case 'reasoner':
      return <ReasonerStep tier={tier} facts={facts} />;
    case 'runner':
      return <RunnerStep tier={tier} />;
    default:
      return <ProjectStep />;
  }
}

function ProviderStep() {
  const create = useCreateProvider();
  const [name, setName] = React.useState('openrouter');
  const [baseUrl, setBaseUrl] = React.useState('https://openrouter.ai/api/v1');
  const [apiKey, setApiKey] = React.useState('');

  return (
    <>
      <div className={styles.formGrid2}>
        <Field label="Name" htmlFor="setup-provider-name">
          <Input id="setup-provider-name" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Base URL" htmlFor="setup-provider-url">
          <Input id="setup-provider-url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
        </Field>
      </div>
      <Field
        label="API key"
        htmlFor="setup-provider-key"
        hint="Stored envelope-encrypted and never echoed back. Needs PRAXIS_MASTER_KEY set."
      >
        <Input
          id="setup-provider-key"
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
        />
      </Field>
      <Button
        variant="primary"
        pending={create.isPending}
        disabled={!name.trim() || !baseUrl.trim() || !apiKey}
        onClick={() => create.mutate({ name: name.trim(), base_url: baseUrl.trim(), api_key: apiKey })}
      >
        Register provider
      </Button>
    </>
  );
}

function ModelStep() {
  const providers = useProviders();
  const create = useCreateModel();
  const [providerId, setProviderId] = React.useState('');
  const [name, setName] = React.useState('');
  const selected = providerId || providers.data?.[0]?.id || '';

  return (
    <>
      <div className={styles.formGrid2}>
        <Field label="Provider" htmlFor="setup-model-provider">
          <Select
            id="setup-model-provider"
            value={selected}
            onChange={(event) => setProviderId(event.target.value)}
            options={(providers.data ?? []).map((p) => ({ value: p.id, label: p.name }))}
          />
        </Field>
        <Field
          label="Model name"
          htmlFor="setup-model-name"
          hint="Exactly as the provider names it, e.g. a :free model for a no-cost run."
        >
          <Input id="setup-model-name" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
      </div>
      <Button
        variant="primary"
        pending={create.isPending}
        disabled={!selected || !name.trim()}
        onClick={() => create.mutate({ providerId: selected, name: name.trim() })}
      >
        Add model
      </Button>
    </>
  );
}

function AgentStep({ tier }: { tier: Tier }) {
  const providers = useProviders();
  const models = useModels();
  const create = useCreateAgent();
  const [name, setName] = React.useState('dev-agent');
  const [runtime, setRuntime] = React.useState('pi');
  const [providerId, setProviderId] = React.useState('');
  const [modelId, setModelId] = React.useState('');

  const provider = providerId || providers.data?.[0]?.id || '';
  const providerModels = (models.data ?? []).filter((m) => m.provider_id === provider);
  const model = modelId || providerModels[0]?.id || '';

  return (
    <>
      <div className={styles.formGrid2}>
        <Field label="Name" htmlFor="setup-agent-name">
          <Input id="setup-agent-name" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field
          label="Runtime"
          htmlFor="setup-agent-runtime"
          hint={tier === 'tier1' ? 'This CLI must be on PATH when the agent runs.' : 'Ignored while the runtime is dry-run.'}
        >
          <Select
            id="setup-agent-runtime"
            value={runtime}
            onChange={(event) => setRuntime(event.target.value)}
            options={[
              { value: 'pi', label: 'pi' },
              { value: 'claude', label: 'claude' },
              { value: 'gemini', label: 'gemini' },
              { value: 'dry-run', label: 'dry-run' },
            ]}
          />
        </Field>
      </div>

      {tier === 'tier1' && (
        <div className={styles.formGrid2}>
          <Field label="Provider" htmlFor="setup-agent-provider">
            <Select
              id="setup-agent-provider"
              value={provider}
              onChange={(event) => setProviderId(event.target.value)}
              options={(providers.data ?? []).map((p) => ({ value: p.id, label: p.name }))}
            />
          </Field>
          <Field label="Model" htmlFor="setup-agent-model">
            <Select
              id="setup-agent-model"
              value={model}
              onChange={(event) => setModelId(event.target.value)}
              options={providerModels.map((m) => ({ value: m.id, label: m.name }))}
            />
          </Field>
        </div>
      )}

      <Button
        variant="primary"
        pending={create.isPending}
        disabled={!name.trim() || (tier === 'tier1' && (!provider || !model))}
        onClick={() =>
          create.mutate({
            name: name.trim(),
            // The generalist defaults. Specialist roles, capability bindings and
            // model tiers stay in the Agents section rather than being asked
            // for during a first run.
            role: 'implementer',
            model_role: 'smart',
            runtime_type: runtime,
            ...(tier === 'tier1' ? { provider_id: provider, model_id: model } : {}),
          })
        }
      >
        Create agent
      </Button>
    </>
  );
}

function DefaultAgentStep() {
  const agents = useAgents();
  const setDefault = useSetDefaultAgent();
  const [agentId, setAgentId] = React.useState('');
  const selected = agentId || agents.data?.[0]?.id || '';

  return (
    <>
      <Field
        label="Default agent"
        htmlFor="setup-default-agent"
        hint="The fallback for any task no capability covers."
      >
        <Select
          id="setup-default-agent"
          value={selected}
          onChange={(event) => setAgentId(event.target.value)}
          options={(agents.data ?? []).map((agent) => ({ value: agent.id, label: agent.name }))}
        />
      </Field>
      <Button
        variant="primary"
        pending={setDefault.isPending}
        disabled={!selected}
        onClick={() => setDefault.mutate(selected)}
      >
        Set as default
      </Button>
    </>
  );
}

function ReasonerStep({ tier, facts }: { tier: Tier; facts: SetupFacts }) {
  const providers = useProviders();
  const models = useModels();
  const save = useSetConfigKey(SCOPE);
  const [providerId, setProviderId] = React.useState(facts.reasonerProviderId);
  const [modelId, setModelId] = React.useState(facts.reasonerModelId);

  const provider = providerId || providers.data?.[0]?.id || '';
  const providerModels = (models.data ?? []).filter((m) => m.provider_id === provider);
  const model = modelId || providerModels[0]?.id || '';

  if (tier === 'tier0') {
    return (
      <>
        <p className={styles.sectionDesc}>
          Stub planning is deterministic and free. Set the mode back to <code>stub</code> to
          continue.
        </p>
        <Button
          variant="primary"
          pending={save.isPending}
          onClick={() => save.mutate({ key: 'reasoner.mode', value: 'stub' })}
        >
          Use the stub reasoner
        </Button>
      </>
    );
  }

  return (
    <>
      <div className={styles.formGrid2}>
        <Field label="Provider" htmlFor="setup-reasoner-provider">
          <Select
            id="setup-reasoner-provider"
            value={provider}
            onChange={(event) => setProviderId(event.target.value)}
            options={(providers.data ?? []).map((p) => ({ value: p.id, label: p.name }))}
          />
        </Field>
        <Field label="Model" htmlFor="setup-reasoner-model">
          <Select
            id="setup-reasoner-model"
            value={model}
            onChange={(event) => setModelId(event.target.value)}
            options={providerModels.map((m) => ({ value: m.id, label: m.name }))}
          />
        </Field>
      </div>
      <Button
        variant="primary"
        pending={save.isPending}
        disabled={!provider || !model}
        onClick={async () => {
          // Order matters: mode last, so the config is never briefly `llm`
          // pointing at a provider or model it has not been given yet.
          await save.mutateAsync({ key: 'reasoner.provider_id', value: provider });
          await save.mutateAsync({ key: 'reasoner.model_id', value: model });
          await save.mutateAsync({ key: 'reasoner.mode', value: 'llm' });
        }}
      >
        Plan with this model
      </Button>
    </>
  );
}

function RunnerStep({ tier }: { tier: Tier }) {
  const save = useSetConfigKey(SCOPE);
  const target = tier === 'tier1' ? 'real' : 'dry-run';

  return (
    <>
      <p className={styles.sectionDesc}>
        {tier === 'tier1'
          ? 'Real agent runs execute the CLI each agent is bound to, against your repository.'
          : 'Dry-run exercises the whole lifecycle — gates, retries, promotion — without spending a token.'}
      </p>
      <Button
        variant="primary"
        pending={save.isPending}
        onClick={() => save.mutate({ key: 'agent_runner.mode', value: target })}
      >
        Set agent runtime to {target}
      </Button>
    </>
  );
}

function ProjectStep() {
  const create = useCreateProject();
  const [name, setName] = React.useState('');
  const [repoUrl, setRepoUrl] = React.useState('');

  return (
    <>
      <div className={styles.formGrid2}>
        <Field label="Name" htmlFor="setup-project-name">
          <Input id="setup-project-name" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field
          label="Repository"
          htmlFor="setup-project-repo"
          hint="A path to an existing Git repository, or https:// / ssh:// remote. Leave blank for a scratch repo."
        >
          <Input
            id="setup-project-repo"
            value={repoUrl}
            onChange={(e) => setRepoUrl(e.target.value)}
            placeholder="/home/you/code/my-project"
          />
        </Field>
      </div>
      <Button
        variant="primary"
        pending={create.isPending}
        disabled={!name.trim()}
        onClick={() => create.mutate({ name: name.trim(), repo_url: repoUrl.trim() || null })}
      >
        Create project
      </Button>
    </>
  );
}
