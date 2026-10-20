import { useCallback, useMemo, useState } from 'react';
import PopupModal from '../../components/common/PopupModal';
import JsonCodeEditor from '../../components/common/JsonCodeEditor';
import MarkdownContent from '../../components/common/MarkdownContent';
import InlineFailure from '../../components/common/InlineFailure';
import {
  PaneEmptyState,
  PaneListFilters,
  PaneListRows,
  PaneListSearch,
  PaneListSortButton,
  PaneListToolbar,
} from '../../components/layout/PaneList';
import DetailHeader, {
  ConfigureActionIcon,
  CopyActionIcon,
  DeleteActionIcon,
  DetailHeaderAction,
  DetailHeaderActions,
  DetailStatus,
  DetailTabs,
  PlayActionIcon,
} from '../../components/layout/DetailHeader';
import StatusPill from '../../components/chat/StatusPill';
import { useNotifications } from '../../context/NotificationContext';
import { renderMarkdownToHtml } from '../../lib/markdown';
import './DesignLibraryPatterns.css';

const FILTERS = ['All', 'Running', 'Paused'];
const SORT_OPTIONS = [
  { value: 'activity', label: 'Last activity' },
  { value: 'name', label: 'Name' },
  { value: 'status', label: 'Status' },
];
const WORK_ITEMS = [
  { id: 'research', name: 'Research release notes', status: 'running', detail: 'Documentation', activity: 1, time: 'now' },
  { id: 'migration', name: 'Review migration plan', status: 'paused', detail: 'Architecture', activity: 12, time: '12m' },
  { id: 'verification', name: 'Verify acceptance criteria', status: 'running', detail: 'Quality', activity: 35, time: '35m' },
];
const DETAIL_TABS = ['Overview', 'Activity', 'Files'];
const CHAT_HTML = renderMarkdownToHtml('I found **three** items ready for review.\n\n- Detail header actions\n- List filtering\n- Notification feedback');

function Caption({ kind, source }) {
  return (
    <p className="dlp-caption">
      <span>{kind}</span>
      <code>{source}</code>
    </p>
  );
}

function WorkRow({ item, selected, onSelect }) {
  return (
    <button
      type="button"
      className={`sess-row dlp-work-row ${selected ? 'is-selected' : ''}`}
      onClick={() => onSelect(item.id)}
    >
      <span className="sess-row__top">
        <span className={`sess-row__dot dlp-work-row__dot dlp-work-row__dot--${item.status}`} />
        <span className="sess-row__agent">{item.name}</span>
      </span>
      <span className="sess-row__age">{item.time}</span>
      <span className="sess-row__title">{item.detail}</span>
      <span className="sess-row__msgs">{item.status === 'running' ? 'Active' : 'Paused'}</span>
    </button>
  );
}

function NavigationPatterns() {
  const [treeOpen, setTreeOpen] = useState(true);
  const [selectedDocument, setSelectedDocument] = useState('release-notes');
  const [menuMessage, setMenuMessage] = useState('');
  const [expanded, setExpanded] = useState(false);
  return (
    <section id="library-navigation" className="dlp-section" aria-labelledby="library-navigation-title">
      <header className="dlp-section__header">
        <div><p className="dlp-section__eyebrow">Navigation</p><h2 id="library-navigation-title">Wayfinding and disclosure</h2></div>
        <Caption kind="shared component + existing markup patterns" source="components/layout/DetailHeader.jsx · pages/PluginsPage.jsx · pages/DashboardPage.jsx" />
      </header>
      <div className="dlp-navigation-grid">
        <article className="dlp-card">
          <h3>Breadcrumbs</h3>
          <nav aria-label="Breadcrumb">
            <ol className="dlp-breadcrumbs">
              <li><a href="#library-navigation">Workspace</a></li>
              <li><a href="#library-layouts">Agents</a></li>
              <li aria-current="page">Research release notes</li>
            </ol>
          </nav>
          <p className="dlp-note">Use for a concrete location; this application does not currently export a breadcrumb component.</p>
        </article>

        <article className="dlp-card dlp-tree-card">
          <div className="dlp-card__head"><h3>Tree rows</h3><span className="dlp-pattern-tag">existing markup pattern</span></div>
          <button type="button" className="dlp-tree-toggle" aria-expanded={treeOpen} onClick={() => setTreeOpen((value) => !value)}>
            <span aria-hidden="true">{treeOpen ? '▾' : '▸'}</span> Documentation
          </button>
          {treeOpen ? (
            <ul className="dlp-tree" aria-label="Documentation files">
              <li><button type="button" aria-pressed={selectedDocument === 'release-notes'} className={`plugins-tree-leaf ${selectedDocument === 'release-notes' ? 'active' : ''}`} onClick={() => setSelectedDocument('release-notes')}><span className="plugins-tree-file pane-row-title">● Release notes</span><span className="plugins-tree-file-path pane-row-caption">docs/releases.md</span></button></li>
              <li><button type="button" aria-pressed={selectedDocument === 'architecture'} className={`plugins-tree-leaf ${selectedDocument === 'architecture' ? 'active' : ''}`} onClick={() => setSelectedDocument('architecture')}><span className="plugins-tree-file pane-row-title">● Architecture</span><span className="plugins-tree-file-path pane-row-caption">docs/architecture.md</span></button></li>
            </ul>
          ) : null}
          <p className="dlp-note" role="status">Selected: {selectedDocument === 'release-notes' ? 'Release notes' : 'Architecture'}.</p>
        </article>

        <article className="dlp-card">
          <div className="dlp-card__head"><h3>Action menu</h3><span className="dlp-pattern-tag">shared component</span></div>
          <DetailHeader
            className="dlp-menu-header"
            title="Fixture actions"
            actions={(
              <DetailHeaderActions menuLabel="Fixture actions">
                <DetailHeaderAction icon={<ConfigureActionIcon />} label="Configure" ariaLabel="Configure fixture" onClick={() => setMenuMessage('Configure selected.')} />
                <DetailHeaderAction icon={<CopyActionIcon />} label="Duplicate" ariaLabel="Duplicate fixture" onClick={() => setMenuMessage('Duplicate selected.')} />
                <DetailHeaderAction icon={<DeleteActionIcon />} label="Archive" ariaLabel="Archive fixture" variant="danger" onClick={() => setMenuMessage('Archive selected.')} />
              </DetailHeaderActions>
            )}
          />
          <p className="dlp-note">The real DetailHeader overflow menu appears when this card is narrow.</p>
          {menuMessage ? <p className="dlp-note" role="status">{menuMessage}</p> : null}
          <button type="button" className="dashboard-collapsible__trigger dlp-collapse" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
            <span>Runtime details</span><span aria-hidden="true">{expanded ? '▾' : '▸'}</span>
          </button>
          {expanded ? <p className="dlp-note">A native button and <code>aria-expanded</code> keep the disclosure behaviour explicit.</p> : null}
        </article>
      </div>
    </section>
  );
}

function LayoutPatterns() {
  const [filter, setFilter] = useState('All');
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState('activity');
  const [selectedId, setSelectedId] = useState(WORK_ITEMS[0].id);
  const [tab, setTab] = useState('Overview');
  const [actionMessage, setActionMessage] = useState('');
  const visibleItems = useMemo(() => {
    const filtered = WORK_ITEMS.filter((item) => (
      (filter === 'All' || item.status === filter.toLowerCase())
      && item.name.toLowerCase().includes(query.toLowerCase())
    ));
    return [...filtered].sort((a, b) => (sort === 'name' ? a.name.localeCompare(b.name) : sort === 'status' ? a.status.localeCompare(b.status) : a.activity - b.activity));
  }, [filter, query, sort]);
  const selected = WORK_ITEMS.find((item) => item.id === selectedId) || visibleItems[0];

  return (
    <section id="library-layouts" className="dlp-section" aria-labelledby="library-layouts-title">
      <header className="dlp-section__header">
        <div><p className="dlp-section__eyebrow">Layouts</p><h2 id="library-layouts-title">List and detail</h2></div>
        <Caption kind="shared components" source="components/layout/PaneList.jsx · components/layout/DetailHeader.jsx" />
      </header>
      <div className="dlp-tabs-sample">
        <span>DetailTabs</span>
        <DetailTabs tabs={DETAIL_TABS} activeTab={tab} onTabChange={setTab} label="Standalone detail tab sample" />
      </div>
      <div className="dlp-list-detail" aria-label="Bounded list and detail example">
        <section className="dlp-list-pane" aria-label="Work items">
          <PaneListFilters filters={FILTERS} activeFilter={filter} onFilterChange={setFilter} />
          <PaneListToolbar>
            <PaneListSearch value={query} placeholder="Search work items..." onChange={setQuery} />
            <PaneListSortButton title="Sort work items" options={SORT_OPTIONS} value={sort} onSelect={setSort} />
          </PaneListToolbar>
          {visibleItems.length ? (
            <PaneListRows
              items={visibleItems}
              getItemKey={(item) => item.id}
              renderItem={(item) => <WorkRow item={item} selected={item.id === selected?.id} onSelect={setSelectedId} />}
            />
          ) : <PaneEmptyState>No work items match this view.</PaneEmptyState>}
        </section>
        <section className="dlp-detail-pane" aria-label="Selected work item">
          {selected ? (
            <>
              <DetailHeader
                className="dlp-detail-header"
                eyebrow="Work item"
                title={selected.name}
                subtitle="Local catalogue fixture"
                tabs={DETAIL_TABS}
                activeTab={tab}
                onTabChange={setTab}
                meta={[<DetailStatus key="status" tone={selected.status === 'running' ? 'ok' : 'muted'}>{selected.status === 'running' ? 'Running' : 'Paused'}</DetailStatus>, <span key="area" className="muted">{selected.detail}</span>]}
                actions={(
                  <DetailHeaderActions menuLabel="Work item actions">
                    <DetailHeaderAction icon={<ConfigureActionIcon />} label="Configure" ariaLabel="Configure work item" onClick={() => setActionMessage('Configure action selected.')} />
                    <DetailHeaderAction icon={<CopyActionIcon />} label="Duplicate" ariaLabel="Duplicate work item" onClick={() => setActionMessage('Duplicate action selected.')} />
                    <DetailHeaderAction icon={<PlayActionIcon />} label="Run" ariaLabel="Run work item" variant="primary" onClick={() => setActionMessage('Run is intentionally inert in the catalogue.')} />
                    <DetailHeaderAction icon={<DeleteActionIcon />} label="Archive" ariaLabel="Archive work item" variant="danger" onClick={() => setActionMessage('Archive action selected.')} />
                  </DetailHeaderActions>
                )}
              />
              <div className="dlp-detail-body">
                <p><strong>{tab}</strong> uses the actual DetailHeader tabs, status treatment, actions, and responsive overflow menu. Resize this bounded example to expose the overflow trigger.</p>
                {actionMessage ? <p className="dlp-inline-status" role="status">{actionMessage}</p> : null}
              </div>
            </>
          ) : <PaneEmptyState detail>Select a work item.</PaneEmptyState>}
        </section>
      </div>
    </section>
  );
}

function FeedbackPatterns() {
  const { show } = useNotifications();
  const [modalOpen, setModalOpen] = useState(false);
  const [progress, setProgress] = useState(60);
  const closeModal = useCallback(() => setModalOpen(false), []);

  return (
    <section id="library-feedback" className="dlp-section" aria-labelledby="library-feedback-title">
      <header className="dlp-section__header">
        <div><p className="dlp-section__eyebrow">Feedback</p><h2 id="library-feedback-title">States and confirmations</h2></div>
        <Caption kind="shared component + existing markup patterns" source="components/common/PopupModal.jsx · context/NotificationContext.jsx · pages/TasksPage.jsx" />
      </header>
      <div className="dlp-feedback-grid">
        <article className="dlp-card">
          <div className="dlp-card__head"><h3>Modal and toast</h3><span className="dlp-pattern-tag">shared components</span></div>
          <p className="dlp-note">Both controls are real, but their effects stay in this catalogue. The shared modal restores focus, handles Escape, and traps Tab focus.</p>
          <div className="dlp-button-row">
            <button type="button" className="btn btn-primary" onClick={() => setModalOpen(true)}>Open confirmation</button>
            <button type="button" className="btn" onClick={() => show('success', 'Catalogue saved', 'This is a local notification demo.')}>Show toast</button>
          </div>
        </article>
        <article className="dlp-card">
          <div className="dlp-card__head"><h3>Alerts and loading</h3><span className="dlp-pattern-tag">shared component + markup</span></div>
          <InlineFailure failure={{ code: 'NETWORK_ERROR' }} operation="Load records" />
          <div className="dlp-loading" role="status"><span className="logs-loading-spinner" aria-hidden="true" /> Loading catalogue data…</div>
          <PaneEmptyState>Nothing needs attention.</PaneEmptyState>
        </article>
        <article className="dlp-card">
          <div className="dlp-card__head"><h3>Progress</h3><span className="dlp-pattern-tag">existing markup pattern</span></div>
          <label className="dlp-progress-label" htmlFor="catalogue-progress">Indexing fixtures <output>{progress}%</output></label>
          <div className="task-progress" aria-hidden="true"><span style={{ width: `${progress}%` }} /></div>
          <input id="catalogue-progress" type="range" min="0" max="100" value={progress} onChange={(event) => setProgress(Number(event.target.value))} />
        </article>
      </div>
      {modalOpen ? <PopupModal eyebrow="Local demonstration" title="Archive this fixture?" message="No item is changed; this only demonstrates the shared modal's focus restoration and Escape handling." confirmLabel="Archive fixture" onClose={closeModal} onConfirm={closeModal} /> : null}
    </section>
  );
}

function DataPatterns() {
  const [jsonDraft, setJsonDraft] = useState('{\n  "workflow": "Cloud review",\n  "approval": true,\n  "agents": ["researcher", "reviewer"]\n}');
  return (
    <section id="library-data" className="dlp-section" aria-labelledby="library-data-title">
      <header className="dlp-section__header">
        <div><p className="dlp-section__eyebrow">Data</p><h2 id="library-data-title">Readable records and activity</h2></div>
        <Caption kind="existing markup patterns + shared component" source="components/chat/Message.jsx · lib/markdown.js · pages/TasksPage.jsx · components/chat/StatusPill.jsx · styles/chrome-frame.css" />
      </header>
      <div className="dlp-data-grid">
        <article className="dlp-card dlp-table-card">
          <div className="dlp-card__head"><h3>Semantic table</h3><span className="dlp-pattern-tag">existing markup pattern</span></div>
          <div className="message-content dlp-table-scroll">
            <table>
              <caption className="dlp-sr-only">Recent workflow runs</caption>
              <thead><tr><th scope="col">Run</th><th scope="col">Status</th><th scope="col">Duration</th></tr></thead>
              <tbody>
                <tr><td>Release notes</td><td><StatusPill label="Complete" tone="ok" /></td><td>1m 42s</td></tr>
                <tr><td>Security review</td><td><StatusPill label="Running" tone="pending" /></td><td>—</td></tr>
                <tr><td>Publish docs</td><td><StatusPill label="Blocked" tone="err" /></td><td>14s</td></tr>
              </tbody>
            </table>
          </div>
        </article>
        <article className="dlp-card">
          <div className="dlp-card__head"><h3>Activity timeline</h3><span className="dlp-pattern-tag">existing markup pattern</span></div>
          <ol className="dlp-timeline">
            <li><span className="task-act-avatar dlp-avatar--system">S</span><p><strong>System</strong> created the review <time>just now</time></p></li>
            <li><span className="task-act-avatar dlp-avatar--user">T</span><p><strong>Tama</strong> added acceptance criteria <time>4m ago</time></p></li>
            <li><span className="task-act-avatar dlp-avatar--agent">A</span><p><strong>Researcher</strong> returned a summary <time>12m ago</time></p></li>
          </ol>
        </article>
        <article className="dlp-card">
          <div className="dlp-card__head"><h3>Lightweight chart</h3><span className="dlp-pattern-tag">existing markup pattern</span></div>
          <div className="ins-stack-chart" role="img" aria-label="Activity bars rising from 24 to 78 percent over five intervals">
            <span style={{ height: '24%' }} /><span style={{ height: '45%' }} /><span style={{ height: '38%' }} /><span style={{ height: '66%' }} /><span style={{ height: '78%' }} />
          </div>
          <p className="dlp-note">Uses the existing CSS bar treatment; no chart library wrapper is exported.</p>
        </article>
        <article className="dlp-card">
          <div className="dlp-card__head"><h3>Chat response</h3><span className="dlp-pattern-tag">existing markup pattern</span></div>
          <article className="message-wrapper assistant msg msg--assistant entry entry--assistant">
            <div className="entry__gutter" aria-hidden="true"><span className="entry__badge entry__badge--assistant">P</span></div>
            <div className="entry__main msg__entry-main">
              <div className="msg__head entry__head"><span className="entry__tag entry__tag--assistant">agent</span><span className="msg__role">Progenitor</span></div>
              <div className="message assistant"><div className="message-content" dangerouslySetInnerHTML={{ __html: CHAT_HTML }} /></div>
            </div>
          </article>
          <p className="dlp-note">Local markdown fixture using the chat message markup and renderer; it does not open a session or call an agent.</p>
        </article>
        <article className="dlp-card">
          <div className="dlp-card__head"><h3>JSON editor</h3><span className="dlp-pattern-tag">shared component</span></div>
          <div className="dlp-json-editor">
            <JsonCodeEditor value={jsonDraft} onChange={(event) => setJsonDraft(event.target.value)} ariaLabel="Catalogue JSON source" />
          </div>
          <Caption kind="shared component" source="components/common/JsonCodeEditor.jsx" />
          <p className="dlp-note">The workflow and routine editors share this highlighted input. Edits here remain local.</p>
        </article>
        <article className="dlp-card">
          <div className="dlp-card__head"><h3>Markdown content</h3><span className="dlp-pattern-tag">shared component</span></div>
          <MarkdownContent content={'### Review before release\n\nUse **shared components** for consistent pages.\n\n- Keep domain actions in the page\n- Reuse the presentation layer\n\n[Open foundations](#library-foundations)'} />
          <Caption kind="shared component" source="components/common/MarkdownContent.jsx" />
          <p className="dlp-note">Read-only content used by instructions, knowledge, plugin previews and quick chat. Document navigation and diagram support stay with their callers.</p>
        </article>
      </div>
    </section>
  );
}

export default function DesignLibraryPatterns() {
  return (
    <>
      <NavigationPatterns />
      <LayoutPatterns />
      <FeedbackPatterns />
      <DataPatterns />
    </>
  );
}
