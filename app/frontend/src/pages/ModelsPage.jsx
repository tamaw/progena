import { useMemo, useState } from 'react';
import {
  PaneEmptyState,
  PaneList,
  PaneListFilters,
  PaneListToolbar,
  PaneListSearch,
  PaneListSortButton,
  PaneListRows,
} from '../components/layout/PaneList';
import PageShell from '../components/layout/PageShell';
import DetailPane from '../components/layout/DetailPane';
import DetailHeader, {
  DetailHeaderActions,
  DetailHeaderAction,
  DetailStatus,
  ConfigureActionIcon,
  DuplicateActionIcon,
  PlayActionIcon,
} from '../components/layout/DetailHeader';

// Static catalogue of the LLM models agents can be assigned. Grouped by provider
// in the list, mirroring the Agents page layout.
const MODELS = [
  {
    id: 'claude-opus-4-8',
    name: 'Claude Opus 4.8',
    provider: 'Anthropic',
    family: 'Claude 4',
    status: 'available',
    isDefault: true,
    context: '200K',
    maxOutput: '64K',
    cutoff: 'Jan 2026',
    modality: ['Text', 'Vision'],
    capabilities: { tools: true, vision: true, streaming: true, json: true, reasoning: true },
    pricing: { input: '$5.00', output: '$25.00' },
    description:
      'Anthropic’s most capable model. Best for complex reasoning, long-horizon agentic work, and code generation where quality matters more than latency.',
    usedBy: ['Code Reviewer', 'Researcher'],
  },
  {
    id: 'claude-sonnet-4-6',
    name: 'Claude Sonnet 4.6',
    provider: 'Anthropic',
    family: 'Claude 4',
    status: 'available',
    context: '200K',
    maxOutput: '64K',
    cutoff: 'Jan 2026',
    modality: ['Text', 'Vision'],
    capabilities: { tools: true, vision: true, streaming: true, json: true, reasoning: true },
    pricing: { input: '$3.00', output: '$15.00' },
    description:
      'Balanced model trading a little quality for noticeably lower cost and latency. A strong default for most agent workloads.',
    usedBy: ['Hello World'],
  },
  {
    id: 'claude-haiku-4-5',
    name: 'Claude Haiku 4.5',
    provider: 'Anthropic',
    family: 'Claude 4',
    status: 'available',
    context: '200K',
    maxOutput: '32K',
    cutoff: 'Jan 2026',
    modality: ['Text', 'Vision'],
    capabilities: { tools: true, vision: true, streaming: true, json: true, reasoning: false },
    pricing: { input: '$0.80', output: '$4.00' },
    description:
      'Fastest, cheapest Claude model. Ideal for high-volume classification, extraction, and lightweight tool-calling agents.',
    usedBy: [],
  },
  {
    id: 'gpt-4o',
    name: 'GPT-4o',
    provider: 'Azure OpenAI',
    family: 'GPT-4',
    status: 'available',
    context: '128K',
    maxOutput: '16K',
    cutoff: 'Oct 2023',
    modality: ['Text', 'Vision', 'Audio'],
    capabilities: { tools: true, vision: true, streaming: true, json: true, reasoning: false },
    pricing: { input: '$2.50', output: '$10.00' },
    description:
      'Multimodal flagship served from the project’s Azure OpenAI deployment. Good general-purpose chat and tool use.',
    usedBy: ['Researcher'],
  },
  {
    id: 'o3',
    name: 'o3',
    provider: 'Azure OpenAI',
    family: 'o-series',
    status: 'preview',
    context: '200K',
    maxOutput: '100K',
    cutoff: 'Oct 2023',
    modality: ['Text', 'Vision'],
    capabilities: { tools: true, vision: true, streaming: false, json: true, reasoning: true },
    pricing: { input: '$2.00', output: '$8.00' },
    description:
      'Reasoning-optimised model that thinks before answering. Best for hard planning, math, and multi-step problem solving.',
    usedBy: [],
  },
  {
    id: 'gemini-2-5-pro',
    name: 'Gemini 2.5 Pro',
    provider: 'Google',
    family: 'Gemini 2.5',
    status: 'preview',
    context: '1M',
    maxOutput: '64K',
    cutoff: 'Jan 2025',
    modality: ['Text', 'Vision', 'Audio'],
    capabilities: { tools: true, vision: true, streaming: true, json: true, reasoning: true },
    pricing: { input: '$1.25', output: '$10.00' },
    description:
      'Very large context window, useful for whole-repository or long-document tasks. Connect a Google provider key to enable.',
    usedBy: [],
  },
];

const MODEL_FILTERS = ['All', 'Available', 'Preview', 'Reasoning'];

const STATUS_DOT = { available: 'running', preview: 'stopped', deprecated: 'failed' };

function matchesModel(model, filter, search) {
  if (filter === 'Available' && model.status !== 'available') return false;
  if (filter === 'Preview' && model.status !== 'preview') return false;
  if (filter === 'Reasoning' && !model.capabilities.reasoning) return false;
  if (search) {
    const haystack = `${model.name} ${model.provider} ${model.family} ${model.id}`.toLowerCase();
    if (!haystack.includes(search.toLowerCase())) return false;
  }
  return true;
}

function MetricCard({ label, value, detail }) {
  return (
    <div className="agents-metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

function CardFact({ label, children }) {
  return (
    <div className="agents-card-fact">
      <span className="agents-card-fact__label">{label}</span>
      <span className="agents-card-fact__value">{children}</span>
    </div>
  );
}

function CapabilityChip({ label, on }) {
  return (
    <span className={`agents-chip ${on ? 'agents-chip--ok' : 'agents-chip--muted'}`}>
      {label}: {on ? 'yes' : 'no'}
    </span>
  );
}

function ModelRow({ model, selected, onSelect }) {
  return (
    <button
      type="button"
      className={`sess-row agents-agent-row agents-agent-row--${model.status} ${selected ? 'is-selected' : ''}`}
      onClick={() => onSelect(model.id)}
    >
      <span className="sess-row__top">
        <span className={`sess-row__dot agents-agent-row__status agents-agent-row__status--${STATUS_DOT[model.status] || 'stopped'}`} />
        <span className="sess-row__agent agents-agent-row__name">{model.name}</span>
        <span className="sess-row__project">{model.provider}</span>
      </span>
      <span className="sess-row__age">{model.context}</span>
      <span className="sess-row__title">{model.family}{model.isDefault ? ' · default' : ''}</span>
      <span className="sess-row__msgs">
        <span><span className="num">{model.pricing.input}</span> in</span>
        <span><span className="num">{model.pricing.output}</span> out</span>
      </span>
    </button>
  );
}

export default function ModelsPage() {
  const [activeFilter, setActiveFilter] = useState('All');
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState(MODELS[0].id);

  const visibleModels = useMemo(
    () => MODELS.filter((model) => matchesModel(model, activeFilter, search)),
    [activeFilter, search],
  );
  const providerOrder = useMemo(
    () => [...new Set(visibleModels.map((model) => model.provider))],
    [visibleModels],
  );
  const grouped = useMemo(() => {
    const groups = {};
    for (const model of visibleModels) {
      (groups[model.provider] ??= []).push(model);
    }
    return groups;
  }, [visibleModels]);

  const selected = MODELS.find((model) => model.id === selectedId) || visibleModels[0] || null;

  return (
    <PageShell>
      <PaneList label="Models" className="agents-pane">
        <PaneListFilters filters={MODEL_FILTERS} activeFilter={activeFilter} onFilterChange={setActiveFilter} />
        <PaneListToolbar>
          <PaneListSearch value={search} placeholder="Search models..." onChange={setSearch} />
          <PaneListSortButton>Provider</PaneListSortButton>
        </PaneListToolbar>
        {visibleModels.length ? (
          <PaneListRows
            groups={providerOrder.map((provider) => ({
              key: provider,
              title: provider,
              items: grouped[provider],
            }))}
            getItemKey={(model) => model.id}
            renderItem={(model) => (
              <ModelRow
                model={model}
                selected={selected?.id === model.id}
                onSelect={setSelectedId}
              />
            )}
          />
        ) : (
          <PaneEmptyState>No models match this view.</PaneEmptyState>
        )}
      </PaneList>

      {selected ? (
        <DetailPane className="detail-pane--dense" label={`${selected.name} model`}>
          <DetailHeader
            title={selected.name}
            actions={(
              <DetailHeaderActions>
                <DetailHeaderAction icon={<ConfigureActionIcon />} label="Configure" tooltip="Configure model" ariaLabel="Configure model" />
                <DetailHeaderAction icon={<DuplicateActionIcon />} label="Duplicate" tooltip="Duplicate model" ariaLabel="Duplicate model" />
                <DetailHeaderAction icon={<PlayActionIcon />} label="Set default" tooltip="Set as default model" ariaLabel="Set as default model" variant="primary" />
              </DetailHeaderActions>
            )}
            meta={[
              <DetailStatus key="status" tone={selected.status === 'available' ? 'ok' : 'info'}>{selected.status === 'available' ? 'Available' : 'Preview'}</DetailStatus>,
              selected.isDefault ? <span key="default" className="muted">Default</span> : null,
              <span key="ctx" className="muted">{selected.context} context</span>,
              <span key="mode" className="muted">{selected.modality.join(' · ')}</span>,
              <code key="id" className="muted">{selected.id}</code>,
            ]}
          />

          <div className="detail-pane__body">
            <div className="agents-metrics">
              <MetricCard label="Context" value={selected.context} detail="input tokens" />
              <MetricCard label="Max output" value={selected.maxOutput} detail="tokens" />
              <MetricCard label="Input" value={selected.pricing.input} detail="per 1M tokens" />
              <MetricCard label="Output" value={selected.pricing.output} detail="per 1M tokens" />
            </div>

            <section className="agents-section">
              <header className="agents-section__head"><h2>About</h2></header>
              <p className="agents-skill-card__desc">{selected.description}</p>
            </section>

            <section className="agents-section">
              <header className="agents-section__head"><h2>Specs</h2></header>
              <div className="agents-card-facts">
                <CardFact label="Provider">{selected.provider}</CardFact>
                <CardFact label="Family">{selected.family}</CardFact>
                <CardFact label="Model ID"><code>{selected.id}</code></CardFact>
                <CardFact label="Knowledge cutoff">{selected.cutoff}</CardFact>
                <CardFact label="Modality">{selected.modality.join(' · ')}</CardFact>
                <CardFact label="Status">{selected.status === 'available' ? 'Available' : 'Preview'}</CardFact>
              </div>
            </section>

            <section className="agents-section">
              <header className="agents-section__head"><h2>Capabilities</h2></header>
              <div className="agents-chip-row">
                <CapabilityChip label="Tool use" on={selected.capabilities.tools} />
                <CapabilityChip label="Vision" on={selected.capabilities.vision} />
                <CapabilityChip label="Streaming" on={selected.capabilities.streaming} />
                <CapabilityChip label="JSON mode" on={selected.capabilities.json} />
                <CapabilityChip label="Reasoning" on={selected.capabilities.reasoning} />
              </div>
            </section>

            <section className="agents-section">
              <header className="agents-section__head"><h2>Used by <span>{selected.usedBy.length} agents</span></h2></header>
              {selected.usedBy.length ? (
                <div className="agents-chip-row">
                  {selected.usedBy.map((agent) => (
                    <span key={agent} className="agents-chip agents-chip--info">{agent}</span>
                  ))}
                </div>
              ) : (
                <PaneEmptyState>No agents are using this model yet.</PaneEmptyState>
              )}
            </section>
          </div>
        </DetailPane>
      ) : (
        <DetailPane className="detail-pane--dense" label="Model">
          <PaneEmptyState detail>Select a model to view its details.</PaneEmptyState>
        </DetailPane>
      )}
    </PageShell>
  );
}
