import { useMemo, useState } from 'react';
import {
  PaneList,
  PaneListFilters,
  PaneListToolbar,
  PaneListSearch,
  PaneListSortButton,
  PaneListScroll,
  PaneListGroup,
} from '../components/layout/PaneList';
import PageShell from '../components/layout/PageShell';
import DetailPane from '../components/layout/DetailPane';
import DetailHeader, {
  DetailHeaderActions,
  DetailHeaderAction,
  DetailStatus,
  ConfigureActionIcon,
  ReconnectActionIcon,
  PlayActionIcon,
} from '../components/layout/DetailHeader';

// Static catalogue of the channels agents can be reached through. Grouped by
// category in the list, mirroring the Models / Agents page layout.
const CHANNELS = [
  {
    id: 'slack',
    name: 'Slack',
    category: 'Team chat',
    status: 'connected',
    handle: '#progenitor',
    description:
      'Talk to agents from a Slack workspace. Mention the bot in a channel or DM it directly; threads keep each conversation separate.',
    agents: ['Code Reviewer', 'Researcher'],
    inbound: 1842,
    outbound: 2104,
    lastActivity: '2m',
    capabilities: { inbound: true, outbound: true, attachments: true, threads: true },
    config: { Workspace: 'progenitor.slack.com', 'Default channel': '#progenitor', Bot: '@progenitor' },
  },
  {
    id: 'teams',
    name: 'Microsoft Teams',
    category: 'Team chat',
    status: 'connected',
    handle: 'Progenitor bot',
    description:
      'Reach agents from Microsoft Teams chats and channels via an Azure Bot registration. Honors your tenant’s compliance policies.',
    agents: ['Researcher'],
    inbound: 612,
    outbound: 740,
    lastActivity: '21m',
    capabilities: { inbound: true, outbound: true, attachments: true, threads: false },
    config: { Tenant: 'progenitor.onmicrosoft.com', 'App ID': 'a1b2…e9f0', Bot: 'Progenitor' },
  },
  {
    id: 'discord',
    name: 'Discord',
    category: 'Team chat',
    status: 'disconnected',
    handle: 'progenitor#0001',
    description:
      'Run agents inside a Discord server. Add the bot, grant message permissions, and it responds to slash commands and mentions.',
    agents: [],
    inbound: 0,
    outbound: 0,
    lastActivity: '—',
    capabilities: { inbound: true, outbound: true, attachments: true, threads: true },
    config: { Server: '—', Bot: '—' },
  },
  {
    id: 'telegram',
    name: 'Telegram',
    category: 'Messaging',
    status: 'connected',
    handle: '@progenitor_bot',
    description:
      'A Telegram bot that forwards direct messages and group mentions to agents. The fastest channel to wire up for personal use.',
    agents: ['Hello World'],
    inbound: 3120,
    outbound: 3098,
    lastActivity: 'now',
    capabilities: { inbound: true, outbound: true, attachments: true, threads: false },
    config: { Bot: '@progenitor_bot', 'Chat ID': '-100…4821' },
  },
  {
    id: 'whatsapp',
    name: 'WhatsApp',
    category: 'Messaging',
    status: 'disconnected',
    handle: '+1 555 0100',
    description:
      'Message agents over WhatsApp Business. Requires a Meta business number and approved message templates for outbound replies.',
    agents: [],
    inbound: 0,
    outbound: 0,
    lastActivity: '—',
    capabilities: { inbound: true, outbound: true, attachments: true, threads: false },
    config: { Number: '—', Provider: 'Meta Cloud API' },
  },
  {
    id: 'email',
    name: 'Email',
    category: 'Email & SMS',
    status: 'connected',
    handle: 'agent@progenitor.dev',
    description:
      'Inbound email is parsed into conversations and replies are sent from the agent address. Good for async, longer-form requests.',
    agents: ['Code Reviewer'],
    inbound: 488,
    outbound: 455,
    lastActivity: '1h',
    capabilities: { inbound: true, outbound: true, attachments: true, threads: true },
    config: { Address: 'agent@progenitor.dev', Transport: 'SMTP + IMAP' },
  },
  {
    id: 'sms',
    name: 'SMS',
    category: 'Email & SMS',
    status: 'error',
    handle: '+1 555 0142',
    description:
      'Two-way SMS through Twilio. Currently erroring — the Twilio auth token is missing or expired. Reconnect to resume delivery.',
    agents: [],
    inbound: 12,
    outbound: 0,
    lastActivity: '3d',
    capabilities: { inbound: true, outbound: true, attachments: false, threads: false },
    config: { Number: '+1 555 0142', Provider: 'Twilio' },
  },
  {
    id: 'webhook',
    name: 'Webhook',
    category: 'Custom',
    status: 'connected',
    handle: 'POST /hooks/agent',
    description:
      'A generic inbound/outbound HTTP channel. Post a JSON message to the endpoint and receive agent replies at your callback URL.',
    agents: ['Researcher'],
    inbound: 9021,
    outbound: 8997,
    lastActivity: 'now',
    capabilities: { inbound: true, outbound: true, attachments: false, threads: false },
    config: { Endpoint: 'https://api.progenitor.dev/hooks/agent', Auth: 'Bearer token' },
  },
];

const CHANNEL_FILTERS = ['All', 'Connected', 'Team chat', 'Messaging'];

const STATUS_DOT = { connected: 'running', disconnected: 'stopped', error: 'failed' };
const STATUS_LABEL = { connected: 'Connected', disconnected: 'Not connected', error: 'Error' };

function matchesChannel(channel, filter, search) {
  if (filter === 'Connected' && channel.status !== 'connected') return false;
  if (filter === 'Team chat' && channel.category !== 'Team chat') return false;
  if (filter === 'Messaging' && channel.category !== 'Messaging') return false;
  if (search) {
    const haystack = `${channel.name} ${channel.category} ${channel.handle}`.toLowerCase();
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

function ChannelRow({ channel, selected, onSelect }) {
  return (
    <button
      type="button"
      className={`sess-row agents-agent-row agents-agent-row--${channel.status} ${selected ? 'is-selected' : ''}`}
      onClick={() => onSelect(channel.id)}
    >
      <span className="sess-row__top">
        <span className={`sess-row__dot agents-agent-row__status agents-agent-row__status--${STATUS_DOT[channel.status] || 'stopped'}`} />
        <span className="sess-row__agent agents-agent-row__name">{channel.name}</span>
        <span className="sess-row__project">{channel.handle}</span>
      </span>
      <span className="sess-row__age">{channel.lastActivity}</span>
      <span className="sess-row__title">{STATUS_LABEL[channel.status]}</span>
      <span className="sess-row__msgs">
        <span><span className="num">{channel.inbound.toLocaleString()}</span> in</span>
        <span><span className="num">{channel.outbound.toLocaleString()}</span> out</span>
        <span><span className="num">{channel.agents.length}</span> agents</span>
      </span>
    </button>
  );
}

export default function ChannelsPage() {
  const [activeFilter, setActiveFilter] = useState('All');
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState(CHANNELS[0].id);

  const visibleChannels = useMemo(
    () => CHANNELS.filter((channel) => matchesChannel(channel, activeFilter, search)),
    [activeFilter, search],
  );
  const categoryOrder = useMemo(
    () => [...new Set(visibleChannels.map((channel) => channel.category))],
    [visibleChannels],
  );
  const grouped = useMemo(() => {
    const groups = {};
    for (const channel of visibleChannels) {
      (groups[channel.category] ??= []).push(channel);
    }
    return groups;
  }, [visibleChannels]);

  const selected = CHANNELS.find((channel) => channel.id === selectedId) || visibleChannels[0] || null;

  return (
    <PageShell>
      <PaneList label="Channels" className="agents-pane">
        <PaneListFilters filters={CHANNEL_FILTERS} activeFilter={activeFilter} onFilterChange={setActiveFilter} />
        <PaneListToolbar>
          <PaneListSearch value={search} placeholder="Search channels..." onChange={setSearch} />
          <PaneListSortButton>Category</PaneListSortButton>
        </PaneListToolbar>
        <PaneListScroll>
          {visibleChannels.length ? (
            categoryOrder.map((category) => (
              <PaneListGroup key={category} title={category} count={grouped[category].length}>
                {grouped[category].map((channel) => (
                  <ChannelRow
                    key={channel.id}
                    channel={channel}
                    selected={selected?.id === channel.id}
                    onSelect={setSelectedId}
                  />
                ))}
              </PaneListGroup>
            ))
          ) : (
            <div className="agents-empty-state">No channels match this view.</div>
          )}
        </PaneListScroll>
      </PaneList>

      {selected ? (
        <DetailPane className="detail-pane--dense" label={`${selected.name} channel`}>
          <DetailHeader
            title={selected.name}
            actions={(
              <DetailHeaderActions>
                <DetailHeaderAction icon={<ConfigureActionIcon />} label="Configure" tooltip="Configure channel" ariaLabel="Configure channel" />
                <DetailHeaderAction icon={<ReconnectActionIcon />} label="Reconnect" tooltip="Reconnect channel" ariaLabel="Reconnect channel" />
                <DetailHeaderAction icon={<PlayActionIcon />} label="Send test" tooltip="Send a test message" ariaLabel="Send a test message" variant="primary" />
              </DetailHeaderActions>
            )}
            meta={[
              <DetailStatus key="status" tone={selected.status === 'connected' ? 'ok' : selected.status === 'error' ? 'error' : 'muted'}>{STATUS_LABEL[selected.status]}</DetailStatus>,
              <span key="cat" className="muted">{selected.category}</span>,
              <span key="handle" className="muted">{selected.handle}</span>,
              <span key="act" className="muted">last activity {selected.lastActivity}</span>,
            ]}
          />

          <div className="detail-pane__body">
            <div className="agents-metrics">
              <MetricCard label="Inbound" value={selected.inbound.toLocaleString()} detail="messages received" />
              <MetricCard label="Outbound" value={selected.outbound.toLocaleString()} detail="messages sent" />
              <MetricCard label="Agents" value={selected.agents.length} detail="routed to" />
              <MetricCard label="Status" value={STATUS_LABEL[selected.status]} detail={`last ${selected.lastActivity}`} />
            </div>

            <section className="agents-section">
              <header className="agents-section__head"><h2>About</h2></header>
              <p className="agents-skill-card__desc">{selected.description}</p>
            </section>

            <section className="agents-section">
              <header className="agents-section__head"><h2>Connection</h2></header>
              <div className="agents-card-facts">
                <CardFact label="Category">{selected.category}</CardFact>
                <CardFact label="Status">{STATUS_LABEL[selected.status]}</CardFact>
                {Object.entries(selected.config).map(([label, value]) => (
                  <CardFact key={label} label={label}><code>{value}</code></CardFact>
                ))}
              </div>
            </section>

            <section className="agents-section">
              <header className="agents-section__head"><h2>Capabilities</h2></header>
              <div className="agents-chip-row">
                <CapabilityChip label="Inbound" on={selected.capabilities.inbound} />
                <CapabilityChip label="Outbound" on={selected.capabilities.outbound} />
                <CapabilityChip label="Attachments" on={selected.capabilities.attachments} />
                <CapabilityChip label="Threads" on={selected.capabilities.threads} />
              </div>
            </section>

            <section className="agents-section">
              <header className="agents-section__head"><h2>Routed to <span>{selected.agents.length} agents</span></h2></header>
              {selected.agents.length ? (
                <div className="agents-chip-row">
                  {selected.agents.map((agent) => (
                    <span key={agent} className="agents-chip agents-chip--info">{agent}</span>
                  ))}
                </div>
              ) : (
                <div className="agents-empty-state">No agents are routed to this channel yet.</div>
              )}
            </section>
          </div>
        </DetailPane>
      ) : (
        <DetailPane className="detail-pane--dense" label="Channel">
          <div className="agents-empty-state agents-empty-state--detail">Select a channel to view its details.</div>
        </DetailPane>
      )}
    </PageShell>
  );
}
