import { useMemo, useState } from 'react';
import DetailHeader, { CopyActionIcon, DetailHeaderAction, DetailHeaderActions, EditActionIcon } from '../components/layout/DetailHeader';
import {
  PaneCollapseSeam,
  PaneExpandRail,
  PaneListFooter,
  PaneListFilters,
  PaneListSearch,
  PaneListSortButton,
  PaneListToolbar,
  PaneListVirtualized,
  PaneSection,
} from '../components/layout/PaneList';
import PageShell from '../components/layout/PageShell';
import DetailPane from '../components/layout/DetailPane';
import { usePaneCollapse } from '../components/layout/usePaneCollapse';

const AGENTS = {
  Codex: { color: '#2f6ff6', initial: 'C' },
  Claude: { color: '#ef6c2a', initial: 'C' },
  Gemini: { color: '#22a06b', initial: 'G' },
  OpenCode: { color: '#a78bfa', initial: 'O' },
  Copilot: { color: '#6f86c4', initial: 'C' },
  Droid: { color: '#e0524a', initial: 'D' },
  OpenClaw: { color: '#c98e5a', initial: 'O' },
};

const SESSION_FILTERS = ['All', ...Object.keys(AGENTS)];
const DETAIL_FILTERS = [
  { label: 'All' },
  { label: 'User', count: '7 / 39', color: 'var(--color-text-muted)' },
  { label: 'OpenCode', count: '0 / 34', color: AGENTS.OpenCode.color },
  { label: 'Tools', count: '0 / 465', color: AGENTS.Gemini.color },
  { label: 'Errors', count: '0 / 15', color: 'var(--color-error)', tone: 'err' },
  { label: 'Images', count: '1 / 7', color: AGENTS.Copilot.color },
];

const FAKE_SESSIONS = [
  {
    id: 'f0d7c2',
    group: 'Today',
    pinned: false,
    agent: 'OpenCode',
    project: 'acme-platform/AgentSessions',
    age: '1h',
    title: 'tool block padding + last line clipping',
    messages: 39,
    tools: 465,
    errors: 15,
    started: 'Wed 09:41 AM',
    duration: '1h 42m',
    model: 'gpt-5-mini',
    tokens: '18.4k',
  },
  {
    id: 'a4f2e1',
    group: 'Today',
    pinned: true,
    agent: 'Codex',
    project: 'acme-platform',
    age: '18m',
    title: 'add canonical_url to todos_get response',
    messages: 94,
    tools: 3,
    errors: 0,
    started: 'Today 10:22 AM',
    duration: '18m',
    model: 'gpt-5.3-codex',
    tokens: '22.1k',
  },
  {
    id: 'b7a912',
    group: 'Today',
    pinned: true,
    agent: 'Claude',
    project: 'tennis-scraper',
    age: '2h',
    title: 'codex is struggling with frontmatter',
    messages: 179,
    tools: 22,
    errors: 1,
    started: 'Today 08:58 AM',
    duration: '2h 10m',
    model: 'sonnet-4.6',
    tokens: '41.2k',
  },
  {
    id: 'pr18',
    group: 'Yesterday',
    pinned: false,
    agent: 'Codex',
    project: 'acme-platform',
    age: 'Wed',
    title: 'review PR 18 and issue 17 fixes',
    messages: 40,
    tools: 12,
    errors: 0,
    started: 'Wed 03:12 PM',
    duration: '44m',
    model: 'gpt-5.3-codex',
    tokens: '11.5k',
  },
  {
    id: 'deploy2102',
    group: 'Yesterday',
    pinned: false,
    agent: 'Gemini',
    project: 'acme-platform',
    age: 'Wed',
    title: 'use deployment skills to deploy 2.10.2',
    messages: 31,
    tools: 8,
    errors: 0,
    started: 'Wed 12:44 PM',
    duration: '31m',
    model: 'gemini-pro',
    tokens: '9.2k',
  },
  {
    id: 'config-sessions',
    group: 'Yesterday',
    pinned: false,
    agent: 'OpenClaw',
    project: 'clawd',
    age: 'Wed',
    title: 'find codex sessions in ~/.config',
    messages: 46,
    tools: 14,
    errors: 0,
    started: 'Wed 09:10 AM',
    duration: '55m',
    model: 'openclaw-local',
    tokens: '8.6k',
  },
  {
    id: 'release2101',
    group: 'Yesterday',
    pinned: false,
    agent: 'Copilot',
    project: 'infra',
    age: 'Tue',
    title: 'prepare release 2.10.01 -- changelog',
    messages: 294,
    tools: 91,
    errors: 2,
    started: 'Tue 02:20 PM',
    duration: '3h 18m',
    model: 'gpt-5.5',
    tokens: '88.9k',
  },
  {
    id: 'structured-todo',
    group: 'Last 2 weeks',
    pinned: false,
    agent: 'Codex',
    project: 'acme-platform',
    age: '2w',
    title: 'research how to implement structured todo',
    messages: 1174,
    tools: 312,
    errors: 4,
    started: 'May 09 11:03 AM',
    duration: '7h 08m',
    model: 'gpt-5.3-codex',
    tokens: '312k',
  },
  {
    id: 'gateway-restart',
    group: 'Last 2 weeks',
    pinned: false,
    agent: 'Droid',
    project: 'acme-platform',
    age: '2w',
    title: 'turn_aborted in long-running tool',
    messages: 516,
    tools: 188,
    errors: 23,
    started: 'May 08 09:48 PM',
    duration: '4h 22m',
    model: 'droid-agent',
    tokens: '154k',
  },
];

const TRANSCRIPT_ENTRIES = [
  {
    id: 'assistant-build',
    kind: 'assistant',
    agent: 'OpenCode',
    tag: 'assistant',
    time: '09:42:11',
    body: (
      <>
        <p>I ran the build with the macOS destination as requested:</p>
        <p><code>xcodebuild -scheme AgentSessions -destination 'platform=macOS' build</code></p>
        <p><strong>Tests</strong></p>
        <ul>
          <li>Not run: <code>AgentSessionsTests</code> (happy to run)</li>
        </ul>
        <p>If you want Timeline / Inspector views to use the same normalized formatting, say the word and I&apos;ll wire them in.</p>
      </>
    ),
  },
  {
    id: 'user-note',
    kind: 'user',
    agent: 'Alex',
    tag: 'user',
    time: '09:48:02',
    note: true,
    body: (
      <>
        <p>[Image #1] in tool outputs -- last line is not fitting into the block. <em>&quot;If auth issues persist, re-run <code>spotify_player authenticate</code> and complete browser login.&quot;</em> Block should have some padding in beginning and end -- for all type of blocks and agents.</p>
        <p>2) Highlight the first line of tool block. 3) It&apos;s still two clicks in tool navigation. 4) Tooltips work fine now.</p>
      </>
    ),
    images: ['spotify_player TUI screenshot', 'tool block truncation'],
  },
  {
    id: 'bash-tool',
    kind: 'tool',
    agent: 'bash',
    tag: 'tool',
    time: '09:51:14',
    tool: {
      kind: 'bash',
      desc: 'spotify_player -- interactive TUI session',
      status: 'aborted',
      statusTone: 'err',
      command: 'spotify_player',
      body: '# tool execution aborted -- try again\n1  Since the TUI is interactive and requires user input, running `spotify_player` directly hangs the session.\n2\n3  **Via TUI:**\n4  Run `spotify_player`, press `/`, search jazz, and press Enter.\n5\n6  If auth issues persist, re-run `spotify_player authenticate` and complete browser login.',
    },
  },
  {
    id: 'grep-tool',
    kind: 'tool',
    agent: 'task',
    tag: 'tool',
    time: '09:53:02',
    tool: {
      kind: 'grep',
      desc: 'rg -n "ToolBlock" AgentSessions/Views/',
      status: 'complete',
      command: 'rg -n "ToolBlock" AgentSessions/Views/',
      body: '42  Views/Session/ToolBlockView.swift:14:struct ToolBlockView: View\n86  Views/Session/ToolBlockView.swift:86: func render(annotations(ranges: ranges))\n126 Views/Session/ToolBlockView.swift:126: func decorations(ranges: [Int: NSRange])',
    },
  },
  {
    id: 'assistant-found',
    kind: 'assistant',
    agent: 'OpenCode',
    tag: 'assistant',
    time: '09:54:38',
    body: (
      <>
        <p>Found it. <code>ToolBlockView</code> uses horizontal padding with no vertical padding on the body. Two changes:</p>
        <ul>
          <li>Bump body padding to <code>EdgeInsets(top: 8, leading: 12, bottom: 10, trailing: 12)</code>.</li>
          <li>Apply italic styling to the first line of every block via the header range.</li>
        </ul>
      </>
    ),
  },
  {
    id: 'build-error',
    kind: 'error',
    agent: 'build',
    tag: 'error',
    time: '10:02:11',
    tool: {
      kind: 'xcodebuild',
      desc: 'platform=macOS · scheme=AgentSessions',
      status: 'exit 65',
      statusTone: 'err',
      body: "1  error: redefinition of 'BlockDecoration'\n2      var out: [TerminalLayoutManager.BlockDecoration] = []\n3                                           ^\n4  ** BUILD FAILED **",
    },
  },
];

function groupSessions(sessions) {
  return sessions.reduce((groups, session) => {
    const group = groups.get(session.group) || [];
    group.push(session);
    groups.set(session.group, group);
    return groups;
  }, new Map());
}

function AgentDot({ agent }) {
  return <span className="adot" style={{ background: AGENTS[agent]?.color || 'var(--color-text-muted)' }} />;
}

function SessionEntry({ entry }) {
  const agentMeta = AGENTS[entry.agent] || AGENTS.OpenCode;
  const badgeClass = entry.kind === 'user' ? 'entry__badge entry__badge--user' : entry.kind === 'tool' ? 'entry__badge entry__badge--tool' : entry.kind === 'error' ? 'entry__badge entry__badge--err' : 'entry__badge';

  return (
    <article className={`entry ${entry.note ? 'entry--note' : ''}`}>
      <div className="entry__gutter">
        <span className={badgeClass} style={entry.kind === 'assistant' ? { background: agentMeta.color } : undefined}>
          {entry.kind === 'tool' ? '⌘' : entry.kind === 'error' ? '!' : agentMeta.initial || 'A'}
        </span>
      </div>
      <div className="entry__main">
        <div className="entry__head">
          <span className={`entry__tag ${entry.kind === 'user' ? 'entry__tag--in' : entry.kind === 'error' ? 'entry__tag--err' : ''}`}>{entry.tag}</span>
          <span className="entry__role">{entry.agent}</span>
          {entry.tool?.desc ? <span className="muted">{entry.tool.desc}</span> : null}
          <span className="entry__time">{entry.time}</span>
        </div>
        {entry.body ? <div className="entry__content">{entry.body}</div> : null}
        {entry.images ? (
          <div className="img-strip">
            {entry.images.map((image, index) => (
              <div key={image} className="img-thumb" data-label={`image_${index + 1}.png`}>
                <div className="img-thumb__inner">{image}</div>
              </div>
            ))}
          </div>
        ) : null}
        {entry.tool ? (
          <div className="tool">
            <div className="tool__head">
              <span className="tool__kind">{entry.tool.kind}</span>
              <span className="tool__desc">{entry.tool.desc}</span>
              <span className={`tool__status ${entry.tool.statusTone === 'err' ? 'tool__status--err' : 'tool__status--ok'}`}>{entry.tool.status}</span>
            </div>
            {entry.tool.command ? <div className="tool__cmd"><span className="prompt">$</span>{entry.tool.command}</div> : null}
            <pre className="tool__body tool__body--out">{entry.tool.body}</pre>
          </div>
        ) : null}
      </div>
    </article>
  );
}

export default function SessionsPage() {
  const [selectedId, setSelectedId] = useState('f0d7c2');
  const [activeAgent, setActiveAgent] = useState('All');
  const [query, setQuery] = useState('');
  const { collapsed, setCollapsed, paneClassName } = usePaneCollapse('sess-list');
  const [activeDetailFilter, setActiveDetailFilter] = useState('All');
  const [activeDetailTab, setActiveDetailTab] = useState('Session');

  const filteredSessions = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return FAKE_SESSIONS.filter((session) => {
      const matchesAgent = activeAgent === 'All' || session.agent === activeAgent;
      const matchesQuery = !normalizedQuery || `${session.title} ${session.agent} ${session.project}`.toLowerCase().includes(normalizedQuery);
      return matchesAgent && matchesQuery;
    });
  }, [activeAgent, query]);

  const groupedSessions = useMemo(() => groupSessions(filteredSessions), [filteredSessions]);
  const selected = FAKE_SESSIONS.find((session) => session.id === selectedId) || filteredSessions[0] || FAKE_SESSIONS[0];
  const selectedAgent = AGENTS[selected.agent] || AGENTS.OpenCode;

  return (
    <PageShell>
      <PaneSection className={paneClassName} aria-label="Sessions" onItemSelect={() => setCollapsed(true)}>
        {collapsed ? (
          <PaneExpandRail label="Sessions" onExpand={() => setCollapsed(false)} />
        ) : (
        <>
        <PaneCollapseSeam label="Sessions" onCollapse={() => setCollapsed(true)} />
        <PaneListFilters
          filters={SESSION_FILTERS}
          activeFilter={activeAgent}
          onFilterChange={setActiveAgent}
          renderFilter={(filter) => (
            <>
              {filter !== 'All' ? <AgentDot agent={filter} /> : null}
              {filter}
            </>
          )}
          actionLabel="+ New"
          onAction={() => {}}
        />
        <PaneListToolbar>
          <PaneListSearch value={query} placeholder="Search sessions" onChange={setQuery} />
          <PaneListSortButton title="Sort">Recent</PaneListSortButton>
        </PaneListToolbar>

        <PaneListVirtualized
          groups={Array.from(groupedSessions.entries()).map(([group, sessions]) => ({
            key: group,
            title: group,
            items: sessions,
          }))}
          getItemKey={(session) => session.id}
          estimatedItemHeight={92}
          renderItem={(session) => {
            const agent = AGENTS[session.agent] || AGENTS.OpenCode;
            return (
              <button
                type="button"
                className={`sess-row ${selected.id === session.id ? 'is-selected' : ''}`}
                onClick={() => setSelectedId(session.id)}
              >
                <span className={`sess-row__star ${session.pinned ? 'is-on' : ''}`}>{session.pinned ? '★' : '☆'}</span>
                <span className="sess-row__top">
                  <span className="sess-row__dot" style={{ background: agent.color }} />
                  <span className="sess-row__agent" style={{ color: agent.color }}>{session.agent}</span>
                  <span className="sess-row__sep">·</span>
                  <span className="sess-row__project">{session.project}</span>
                </span>
                <span className="sess-row__age">{session.age}</span>
                <span className="sess-row__title">{session.title}</span>
                <span className="sess-row__msgs">
                  <span className="num">{session.messages.toLocaleString()}</span> msgs · {session.tools} tools · <span className={session.errors > 0 ? 'err' : ''}>{session.errors} err</span>
                </span>
              </button>
            );
          }}
        />

        <PaneListFooter>
          <span><span className="num">637</span> sessions</span>
          <span>Last <span className="num">18m</span> ago</span>
        </PaneListFooter>
        </>
        )}
      </PaneSection>

      <DetailPane flush className="sess-detail">
        <DetailHeader
          title={selected.title}
          actions={(
            <>
              <DetailHeaderActions>
                <DetailHeaderAction icon={<EditActionIcon />} label="Rename" tooltip="Rename session" ariaLabel="Rename session" />
                <DetailHeaderAction icon={<CopyActionIcon />} label="Copy" tooltip="Copy session details" ariaLabel="Copy session details" />
              </DetailHeaderActions>
              <span className="sess-detail__id">ID {selected.id}</span>
            </>
          )}
          meta={[
            <span key="model" className="sess-detail__model"><AgentDot agent={selected.agent} />{selected.agent} <span className="muted">· {selected.model}</span></span>,
            <span key="project" className="muted">{selected.project}</span>,
            <span key="started" className="muted">Started {selected.started} · {selected.duration}</span>,
            <span key="tokens" className="muted">{selected.messages} messages · {selected.tokens} tokens</span>,
          ]}
          tabs={['Session', 'Text', 'JSON']}
          activeTab={activeDetailTab}
          onTabChange={setActiveDetailTab}
          tabLabel="Session detail tabs"
        />

        {activeDetailTab === 'Session' ? (
          <div className="sess-filters">
            {DETAIL_FILTERS.map((filter) => (
              <button
                key={filter.label}
                type="button"
                className={`sess-fchip ${activeDetailFilter === filter.label ? 'is-active' : ''} ${filter.tone === 'err' ? 'sess-fchip--err' : ''}`}
                onClick={() => setActiveDetailFilter(filter.label)}
              >
                {filter.color ? <span className="adot" style={{ background: filter.color }} /> : null}
                {filter.label}
                {filter.count ? <span className="count">{filter.count}</span> : null}
              </button>
            ))}
          </div>
        ) : null}

        <div className="sess-detail__body" style={{ '--session-agent-color': selectedAgent.color }}>
          {activeDetailTab === 'Session' ? (
            TRANSCRIPT_ENTRIES.map((entry) => <SessionEntry key={entry.id} entry={entry} />)
          ) : (
            <div className="sess-placeholder-pane">
              <div className="sess-placeholder-pane__head">
                <strong>{activeDetailTab} preview</strong>
                <span>mock session export</span>
              </div>
              <pre>
                {activeDetailTab === 'Text'
                  ? TRANSCRIPT_ENTRIES.map((entry) => `[${entry.time}] ${entry.tag}: ${entry.tool?.desc || entry.agent}`).join('\n')
                  : JSON.stringify({ session: selected, entries: TRANSCRIPT_ENTRIES.map(({ id, kind, agent, tag, time }) => ({ id, kind, agent, tag, time })) }, null, 2)}
              </pre>
            </div>
          )}
        </div>
      </DetailPane>
    </PageShell>
  );
}
