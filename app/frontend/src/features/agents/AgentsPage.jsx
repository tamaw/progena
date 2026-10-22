import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useNavigate } from 'react-router-dom';
import { DraftField } from '../../components/DetailCards';
import InlineFailure from '../../components/InlineFailure';
import PopupModal from '../../components/PopupModal';
import Modal from '../../components/Modal';
import useEntityDraft from '../../hooks/useEntityDraft';
import useAgentCatalogue from './useAgentCatalogue';
import useConfirmAction from '../../hooks/useConfirmAction';
import { createUuid } from '../../lib/uuid';
import { requestAcknowledged } from '../../store/http';
import {
  draftChanged,
  draftDiscarded,
  entityDraftKey,
} from '../../store/editorDraftsSlice';
import { deleteAgentProfile, updateObjectAgentProfile } from './state/agents';
import {
  PaneEmptyState,
  PaneList,
  PaneListFilters,
  PaneListFooter,
  PaneListSearch,
  PaneListSortButton,
  PaneListToolbar,
  PaneListRows,
} from '../../components/layout/PaneList';
import PageShell from '../../components/layout/PageShell';
import DetailPane from '../../components/layout/DetailPane';
import DetailHeader, {
  DeleteActionIcon,
  DetailHeaderAction,
  DetailHeaderActions,
  EditActionIcon,
  SaveActionIcon,
} from '../../components/layout/DetailHeader';
import { A2ADetailBody, ACPDetailBody, ObjectAgentDetailBody } from './AgentDetailBodies';
import { formatAge } from './agentFormat';
import { catalogueAgents } from './agentCatalogueModel';

const AGENT_FILTERS = ['All', 'Running', 'Failed', 'Stopped', 'Disabled'];
const SORT_MODES = ['Type', 'Name', 'Recent'];
// Object agents are portable SYSTEM.md manifests. A2A and ACP are protocol
// integrations, not internal agents, and are intentionally absent here.
const AGENT_TYPES = [
  {
    kind: 'object',
    label: 'Object agent',
    blurb: 'Manifest-backed internal agent with a Markdown system prompt and selected tools.',
  },
];

const AGENT_TYPE_LABELS = Object.fromEntries(AGENT_TYPES.map((type) => [type.kind, type.label]));

const NEW_AGENT_SENTINEL = 'create';

function makeDraftAgent(kind) {
  return {
    kind,
    name: '',
    model: '',
    summary: '',
    version: '1.0.0',
    prompt: '',
    tools: '',
    skills: '',
  };
}

function StatusPill({ status }) {
  const label = status || 'draft';
  return (
    <span className={`agents-status-pill agents-status-pill--${label}`}>
      <span className="adot" />
      {label}
    </span>
  );
}

function AgentRow({ agent, selected, onSelect }) {
  // Object agents have no runtime status/enabled concept (see DetailHeader);
  // showing a status dot for them would fabricate a "draft"/"disabled" state
  // that doesn't exist in the manifest.
  const status = agent.kind === 'object' ? null : (agent.enabled === false ? 'disabled' : (agent.status || 'draft'));
  return (
    <button
      type="button"
      className={`sess-row agents-agent-row ${status ? `agents-agent-row--${status}` : ''} ${selected ? 'is-selected' : ''}`}
      onClick={() => onSelect(agent.id)}
    >
      <span className="sess-row__top">
        {status ? <span className={`sess-row__dot agents-agent-row__status agents-agent-row__status--${status}`} /> : null}
        <span className="sess-row__agent agents-agent-row__name">{agent.name}</span>
        <span className="sess-row__project">{agent.model}</span>
      </span>
      <span className="sess-row__age">{formatAge(agent.updated)}</span>
      <span className="sess-row__title">{agent.role}</span>
      <span className="sess-row__msgs">
        <span><span className="num">{(agent.tools || []).length}</span> tools</span>
        <span><span className="num">{(agent.skills || []).length}</span> skills</span>
      </span>
    </button>
  );
}

// Compact type-picker shown by "+ New": choose what kind of agent to scaffold.
// Picking a type opens the editable draft in the detail pane.
function NewAgentModal({ onPick, onClose }) {
  return (
    <Modal
      className="agents-modal"
      backdropClassName="agents-modal-backdrop"
      ariaLabel="New agent"
      backdropDismissEvent="click"
      onClose={onClose}
    >
      <header className="agents-modal__head">
        <h3>New agent</h3>
        <button type="button" className="agents-modal__close" onClick={onClose} aria-label="Close">×</button>
      </header>
      <p className="agents-modal__lead">Pick the kind of agent to create.</p>
      <div className="agents-modal__types">
        {AGENT_TYPES.map((type) => (
          <button
            key={type.kind}
            type="button"
            className={`agents-type-card agents-type-card--${type.kind}`}
            onClick={() => onPick(type.kind)}
          >
            <strong>{type.label}</strong>
            <span>{type.blurb}</span>
          </button>
        ))}
      </div>
    </Modal>
  );
}

// Editable body for a new manifest-backed object agent — the only creatable
// kind (A2A/ACP are protocol integrations wired via config, not created here).
// Fields serialize to YAML front matter plus the Markdown system prompt in
// SYSTEM.md.
function DraftAgentDetailBody({ draft, onChange }) {
  const set = (field) => (event) => onChange(field, event.target.value);
  return (
    <div className="detail-pane__body agents-draft">
      <div className="agents-draft__note">
        Draft {AGENT_TYPE_LABELS[draft.kind]} — fill in the details, then Create. Nothing is saved until you create the agent.
      </div>

      <section className="agents-section">
        <header className="agents-section__head"><h2>Identity</h2></header>
        <DraftField label="Model"><input value={draft.model} onChange={set('model')} placeholder="e.g. gpt-5" /></DraftField>
        <DraftField label="Description">
          <textarea rows="3" value={draft.summary} onChange={set('summary')} placeholder="What this agent is for…" />
        </DraftField>
      </section>

      <section className="agents-section agents-section--prompt">
        <header className="agents-section__head"><h2>System prompt</h2></header>
        <DraftField label="Prompt" hint="The persona and instructions the subagent runs with.">
          <textarea rows="8" value={draft.prompt} onChange={set('prompt')} placeholder="You are…" />
        </DraftField>
      </section>
      <section className="agents-section">
        <header className="agents-section__head"><h2>Connections</h2></header>
        <div className="agents-draft-grid">
          <DraftField label="Tools" hint="Comma-separated tool names.">
            <input value={draft.tools} onChange={set('tools')} placeholder="shell, mcp, a2a_send" />
          </DraftField>
          <DraftField label="Skills" hint="Comma-separated /skills/ source paths.">
            <input value={draft.skills} onChange={set('skills')} placeholder="/skills/review/" />
          </DraftField>
        </div>
      </section>
    </div>
  );
}

export default function AgentsPage() {
  const dispatch = useDispatch();
  const navigate = useNavigate();
  const {
    agents,
    agentsUrl,
    agentsState,
    detailFailure,
    effectiveAgentId,
    rosterFailure,
    retryDetail,
    retryRoster,
    selectAgent,
    selectedAgent,
  } = useAgentCatalogue({ kind: 'agents', routeBase: '/agents', detailSuffix: '/details' });
  const [activeFilter, setActiveFilter] = useState('All');
  const [search, setSearch] = useState('');
  const [sortMode, setSortMode] = useState('Type');
  const createDraftKey = entityDraftKey('agent-create', NEW_AGENT_SENTINEL, 'object');
  const [draftAgent, setDraftAgent, createDraftControls] = useEntityDraft(createDraftKey, null);
  const agentViewKey = entityDraftKey('agents', 'page', 'view');
  const [agentView, setAgentView] = useEntityDraft(agentViewKey, null);
  const showNewAgentDraft = agentView === 'create' && Boolean(draftAgent);
  const objectDraftKey = entityDraftKey('agent-system', effectiveAgentId, 'system');
  const [objectAgentDraft, setObjectAgentDraft, objectDraftControls] = useEntityDraft(objectDraftKey, null);
  const {
    clear: clearCreateDraft,
    beginSave: beginCreateDraftSave,
    trackSave: trackCreateDraftSave,
    failSave: failCreateDraftSave,
    saving: createDraftSaving,
    error: createDraftError,
  } = createDraftControls;
  const {
    clear: clearObjectDraft,
    beginSave: beginObjectDraftSave,
    trackSave: trackObjectDraftSave,
    failSave: failObjectDraftSave,
    hasDraft: hasObjectDraft,
    saving: objectDraftSaving,
    error: objectDraftError,
  } = objectDraftControls;
  const [renameIntent, setRenameIntent] = useState(null);
  // The confirmation effect runs once per intent; a ref (not state) records
  // that, so applying an external confirmation never re-renders from an effect.
  const appliedRenameRef = useRef(null);
  const renameSourceDraft = useSelector((state) => (
    renameIntent
      ? state.editorDrafts?.entries[entityDraftKey('agent-system', renameIntent.agentId, 'system')]
      : null
  ));
  const renameConfirmed = useSelector((state) => (
    renameIntent?.requestId
      && state.realtime?.receivedRequestEvents?.[renameIntent.requestId]?.some(
        (event) => event.type === 'agents.snapshot',
      )
  ));
  const [newAgentOpen, setNewAgentOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const deleteAction = useConfirmAction(effectiveAgentId);

  useEffect(() => {
    if (agentView === 'create' && !draftAgent && !createDraftSaving) {
      setAgentView('selected');
    }
  }, [agentView, createDraftSaving, draftAgent, setAgentView]);

  useEffect(() => {
    if (
      !renameIntent
      || !renameConfirmed
      || appliedRenameRef.current === renameIntent
      || effectiveAgentId !== renameIntent.agentId
    ) {
      return;
    }
    appliedRenameRef.current = renameIntent;

    if (renameSourceDraft) {
      const destinationKey = entityDraftKey('agent-system', renameIntent.newId, 'system');
      dispatch(draftChanged({ key: destinationKey, value: renameSourceDraft.value }));
      dispatch(draftDiscarded(objectDraftKey));
    }
    selectAgent(renameIntent.newId);
  }, [
    dispatch,
    effectiveAgentId,
    objectDraftKey,
    renameConfirmed,
    renameIntent,
    renameSourceDraft,
    selectAgent,
  ]);

  const handleNewAgent = useCallback(() => {
    setNewAgentOpen(true);
  }, []);

  const handlePickType = useCallback((kind) => {
    setDraftAgent((current) => current ?? makeDraftAgent(kind));
    setAgentView('create');
    setNewAgentOpen(false);
  }, [setAgentView, setDraftAgent]);

  const updateDraft = useCallback((field, value) => {
    setDraftAgent((current) => (current ? { ...current, [field]: value } : current));
  }, [setDraftAgent]);

  const discardDraft = useCallback(() => {
    clearCreateDraft();
    setAgentView('selected');
  }, [clearCreateDraft, setAgentView]);

  const createDraft = useCallback(async () => {
    if (!draftAgent || createDraftSaving) return;
    const ticket = beginCreateDraftSave();
    if (!ticket) return;
    const list = (value) => value.split(',').map((item) => item.trim()).filter(Boolean);
    const requestId = createUuid();
    trackCreateDraftSave(ticket, {
      requestId,
      events: ['agents.snapshot'],
    });
    try {
      await requestAcknowledged(`${agentsUrl}/object`, {
        method: 'POST',
        body: {
          name: draftAgent.name,
          description: draftAgent.summary,
          version: draftAgent.version,
          model: draftAgent.model || null,
          system_prompt: draftAgent.prompt,
          tools: list(draftAgent.tools),
          skills: list(draftAgent.skills),
        },
        requestId,
      });
    } catch (error) {
      failCreateDraftSave(ticket, error);
    }
  }, [
    agentsUrl,
    beginCreateDraftSave,
    createDraftSaving,
    draftAgent,
    failCreateDraftSave,
    trackCreateDraftSave,
  ]);

  const handleSelectAgent = useCallback((id) => {
    setAgentView('selected');
    selectAgent(id);
  }, [selectAgent, setAgentView]);

  const requestDeleteAgent = useCallback(() => {
    if (!selectedAgent) return;
    const target = { id: selectedAgent.id, name: selectedAgent.name };
    setDeleteTarget(target);
    deleteAction.request('Delete agent', async ({ isCurrent }) => {
      await dispatch(deleteAgentProfile({ agentsUrl, agentId: target.id })).unwrap();
      if (isCurrent()) navigate('/agents', { replace: true });
    });
  }, [agentsUrl, deleteAction, dispatch, navigate, selectedAgent]);

  const closeDeleteAgent = useCallback(() => {
    if (deleteAction.busy) return;
    deleteAction.close();
    setDeleteTarget(null);
  }, [deleteAction]);

  const { visible: visibleAgents, sections } = useMemo(
    () => catalogueAgents(agents, { filter: activeFilter, search, sortMode }),
    [activeFilter, agents, search, sortMode],
  );
  const isA2A = selectedAgent?.kind === 'a2a';
  const isACP = selectedAgent?.kind === 'acp';
  const runningCount = agents.filter((agent) => agent.status === 'running').length;
  const taskCount = agents.reduce((total, agent) => total + (agent.tasks?.length || 0), 0);

  function slugifyAgentName(value) {
    return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'agent';
  }

  function extractManifestName(content) {
    const frontMatter = content.match(/^---\s*\n([\s\S]*?)\n---/);
    if (!frontMatter) return null;
    const nameLine = frontMatter[1].split('\n').find((line) => /^name:\s*/.test(line));
    if (!nameLine) return null;
    return nameLine.replace(/^name:\s*/, '').trim().replace(/^['"]|['"]$/g, '') || null;
  }

  const startEditObjectAgent = useCallback(() => {
    setObjectAgentDraft(selectedAgent?.card?.raw_content ?? '');
  }, [selectedAgent, setObjectAgentDraft]);

  const cancelEditObjectAgent = useCallback(() => {
    clearObjectDraft();
    setRenameIntent(null);
  }, [clearObjectDraft]);

  const saveObjectAgentDraft = useCallback(async () => {
    if (objectAgentDraft === null || !effectiveAgentId || objectDraftSaving) return;
    const agentId = effectiveAgentId;
    const content = objectAgentDraft;
    const ticket = beginObjectDraftSave();
    if (!ticket) return;
    const name = extractManifestName(content);
    const newId = name ? `object-${slugifyAgentName(name)}` : null;
    const operation = dispatch(updateObjectAgentProfile({
      agentsUrl,
      agentId,
      manifest: { content },
    }));
    setRenameIntent(
      newId && newId !== agentId ? { agentId, newId, requestId: operation.requestId } : null,
    );
    trackObjectDraftSave(ticket, {
      requestId: operation.requestId,
      events: ['agents.snapshot'],
    });
    try {
      await operation.unwrap();
    } catch (error) {
      setRenameIntent(null);
      failObjectDraftSave(ticket, error);
    }
  }, [
    agentsUrl,
    beginObjectDraftSave,
    dispatch,
    effectiveAgentId,
    failObjectDraftSave,
    objectAgentDraft,
    objectDraftSaving,
    trackObjectDraftSave,
  ]);

  return (
    <PageShell>
      <PaneList label="Agents" className="agents-pane">
        <PaneListFilters
          filters={AGENT_FILTERS}
          activeFilter={activeFilter}
          onFilterChange={setActiveFilter}
          actionLabel="+ New"
          onAction={handleNewAgent}
        />
        <PaneListToolbar>
          <PaneListSearch value={search} placeholder="Search agents..." onChange={setSearch} />
          <PaneListSortButton
            title="Sort agents"
            options={SORT_MODES}
            value={sortMode}
            onSelect={setSortMode}
          />
        </PaneListToolbar>
        {rosterFailure ? <InlineFailure failure={rosterFailure} onRetry={retryRoster} /> : null}
        {visibleAgents.length ? (
          <PaneListRows
            groups={sections}
            getGroupItems={(section) => section.items}
            renderItem={(agent) => (
              <AgentRow
                agent={agent}
                selected={selectedAgent?.id === agent.id}
                onSelect={handleSelectAgent}
              />
            )}
          />
        ) : !rosterFailure ? (
          <PaneEmptyState>{agentsState.loading || !agentsState.loaded ? 'Loading agents…' : 'No agents match this view.'}</PaneEmptyState>
        ) : null}
        <PaneListFooter>
          <span><span className="num">{runningCount}</span> running</span>
          <span><span className="num">{taskCount}</span> tasks</span>
        </PaneListFooter>
      </PaneList>

      {showNewAgentDraft && draftAgent ? (
        <DetailPane className="detail-pane--dense agents-detail agents-detail--draft" label="New agent">
          <DetailHeader
            title={(
              <input
                className="agents-draft-title"
                value={draftAgent.name}
                onChange={(event) => updateDraft('name', event.target.value)}
                placeholder={`New ${AGENT_TYPE_LABELS[draftAgent.kind]}`}
                aria-label="Agent name"
              />
            )}
            actions={(
              <DetailHeaderActions>
                <DetailHeaderAction icon={<DeleteActionIcon />} label="Discard" tooltip="Discard draft" ariaLabel="Discard draft" onClick={discardDraft} disabled={createDraftSaving} />
                <DetailHeaderAction icon={<SaveActionIcon />} label={createDraftSaving ? 'Creating…' : 'Create'} tooltip="Create agent" ariaLabel="Create agent" variant="primary" onClick={createDraft} disabled={createDraftSaving} />
              </DetailHeaderActions>
            )}
            meta={[
              <span key="type" className="agents-status-pill agents-status-pill--draft"><span className="adot" />{AGENT_TYPE_LABELS[draftAgent.kind]}</span>,
              <span key="hint" className="muted">Unsaved draft</span>,
            ]}
          />
          <InlineFailure failure={createDraftError} operation="Create agent" />
          <DraftAgentDetailBody draft={draftAgent} onChange={updateDraft} />
        </DetailPane>
      ) : selectedAgent ? (
        <DetailPane className="detail-pane--dense agents-detail" label={`${selectedAgent.name} profile`}>
          <DetailHeader
            title={selectedAgent.name}
            actions={selectedAgent.kind === 'object' && selectedAgent.card?.source !== 'native' ? (
              <DetailHeaderActions menuLabel="Agent actions">
                <DetailHeaderAction
                  icon={<EditActionIcon />}
                  label="Edit"
                  tooltip="Edit manifest"
                  ariaLabel="Edit manifest"
                  onClick={startEditObjectAgent}
                />
                {selectedAgent.card?.source === 'user' ? (
                  <DetailHeaderAction
                    icon={<DeleteActionIcon />}
                    label="Delete"
                    tooltip="Delete agent"
                    ariaLabel="Delete agent"
                    variant="danger"
                    onClick={requestDeleteAgent}
                  />
                ) : null}
              </DetailHeaderActions>
            ) : null}
            meta={[
              // Object agents have no schema-backed status/enabled concept
              // (no runtime process, no manifest field) -- showing a pill
              // here would fabricate a value the backend doesn't have.
              selectedAgent.kind !== 'object'
                ? <StatusPill key="status" status={selectedAgent.enabled === false ? 'disabled' : selectedAgent.status} /> : null,
              // Object agents show Model in the Manifest section and
              // Tools/Skills counts in their own MiniListSection headers
              // below -- repeating them here would be a literal duplicate
              // of data already on the page. A2A/ACP have no such section,
              // so the header remains their only place to show these.
              selectedAgent.kind !== 'object' && selectedAgent.model
                ? <span key="model" className="sess-detail__model">{selectedAgent.model}</span> : null,
              selectedAgent.url ? (
                <a key="url" className="muted agents-agent-url" href={selectedAgent.url} target="_blank" rel="noreferrer">
                  {selectedAgent.transport ? `${selectedAgent.transport} · ` : ''}{selectedAgent.url}
                </a>
              ) : null,
              selectedAgent.kind !== 'object' ? (
                <span key="links" className="muted">{(selectedAgent.skills || []).length ? `${selectedAgent.skills.length} skills · ` : ''}{(selectedAgent.tools || []).length} tools</span>
              ) : null,
            ]}
          />
          {detailFailure ? <InlineFailure failure={detailFailure} onRetry={retryDetail} /> : null}
          {isA2A
            ? <A2ADetailBody agent={selectedAgent} />
            : isACP
              ? <ACPDetailBody agent={selectedAgent} />
              : (
                <ObjectAgentDetailBody
                  agent={selectedAgent}
                  agentsUrl={agentsUrl}
                  editing={hasObjectDraft}
                  draft={objectAgentDraft}
                  onDraftChange={setObjectAgentDraft}
                  onStartEdit={startEditObjectAgent}
                  onCancelEdit={cancelEditObjectAgent}
                  onSaveEdit={saveObjectAgentDraft}
                  saving={objectDraftSaving}
                  error={objectDraftError}
                />
              )}
        </DetailPane>
      ) : (
        <DetailPane className="detail-pane--dense agents-detail" label="Agent profile">
          {detailFailure
            ? <InlineFailure failure={detailFailure} onRetry={retryDetail} />
            : <PaneEmptyState detail>{agentsState.loading || !agentsState.loaded ? 'Loading agents…' : 'Select an agent to view its profile.'}</PaneEmptyState>}
        </DetailPane>
      )}

      {newAgentOpen ? <NewAgentModal onPick={handlePickType} onClose={() => setNewAgentOpen(false)} /> : null}
      {deleteAction.open && deleteTarget?.id === effectiveAgentId ? (
        <PopupModal
          eyebrow="Delete agent"
          title={`Delete “${deleteTarget.name}”?`}
          message="This agent and its manifest will be permanently deleted."
          error={deleteAction.error}
          confirmLabel="Delete agent"
          busyLabel="Deleting…"
          onClose={closeDeleteAgent}
          onConfirm={deleteAction.confirm}
          busy={deleteAction.busy}
        />
      ) : null}
    </PageShell>
  );
}
