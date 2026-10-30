import { UI_EVENT } from '../../realtime/eventVocabulary';
import { useDispatch, useSelector } from 'react-redux';
import useEntityDraft from '../../hooks/useEntityDraft';
import { entityDraftKey } from '../../store/editorDraftsSlice';
import { PaneEmptyState } from '../../components/layout/PaneList';
import { MetricCard, CardFact, CapabilityChip, DraftField } from '../../components/DetailCards';
import InlineFailure from '../../components/InlineFailure';
import { fetchAgentSkillContent, selectAgentSkillContent, updateAgentSkillContent } from './state/agents';
import { formatAge } from './agentFormat';

// Chip tone per A2A task status (matches Progena's A2ATaskStatus enum).
const TASK_TONE = {
  completed: 'ok',
  working: 'info',
  submitted: 'info',
  'input-required': 'info',
  'auth-required': 'info',
  failed: 'error',
  rejected: 'error',
  canceled: 'muted',
  unknown: 'muted',
};

function MiniListSection({ title, count, children, action }) {
  return (
    <section className="agents-section">
      <header className="agents-section__head">
        <h2>{title} <span>{count}</span></h2>
        {action ? <button type="button" className="repo-btn repo-btn--ghost">{action}</button> : null}
      </header>
      {children}
    </section>
  );
}

function ConnectionRow({ icon, title, detail, meta, tone }) {
  return (
    <div className="agents-connection-row">
      <span className="agents-connection-row__icon">{icon}</span>
      <div className="agents-connection-row__body">
        <strong>{title}</strong>
        <p>{detail}</p>
      </div>
      <span className={`agents-chip ${tone ? `agents-chip--${tone}` : ''}`}>{meta}</span>
    </div>
  );
}

// Detail pane for A2A agents: everything shown is sourced from the live agent
// card (capabilities, transport, protocol, interaction modes, skills), not the
// subagent definition fields, which do not apply to a remote A2A server.
export function A2ADetailBody({ agent }) {
  const card = agent.card || {};
  const capabilities = card.capabilities || {};
  const inputModes = card.inputModes || [];
  const outputModes = card.outputModes || [];
  const provider = card.provider || {};
  const skills = agent.skills || [];
  const tasks = agent.tasks || [];
  const checkpoints = agent.metrics?.checkpoints ?? new Set(tasks.map((t) => t.context_id).filter(Boolean)).size;

  return (
    <div className="detail-pane__body">
      <div className="agents-metrics">
        <MetricCard label="Skills" value={skills.length} detail="advertised" />
        <MetricCard label="Tasks" value={agent.metrics?.tasks ?? tasks.length} detail="A2A runs" />
        <MetricCard label="Checkpoints" value={checkpoints} detail="conversation threads" />
        <MetricCard label="Protocol" value={card.protocolVersion || '—'} detail="A2A version" />
      </div>

      <section className="agents-section">
        <header className="agents-section__head"><h2>Agent card</h2></header>
        <div className="agents-card-facts">
          {agent.summary ? <CardFact label="Description">{agent.summary}</CardFact> : null}
          <CardFact label="Availability">{agent.enabled === false ? 'Disabled' : 'Enabled'}</CardFact>
          {agent.url ? (
            <CardFact label="Endpoint">
              <a className="agents-agent-url" href={agent.url} target="_blank" rel="noreferrer">{agent.url}</a>
            </CardFact>
          ) : null}
          {provider.organization ? (
            <CardFact label="Provider">
              {provider.url ? (
                <a className="agents-agent-url" href={provider.url} target="_blank" rel="noreferrer">{provider.organization}</a>
              ) : provider.organization}
            </CardFact>
          ) : null}
          {card.documentationUrl ? (
            <CardFact label="Docs">
              <a className="agents-agent-url" href={card.documentationUrl} target="_blank" rel="noreferrer">{card.documentationUrl}</a>
            </CardFact>
          ) : null}
        </div>
      </section>

      <section className="agents-section">
        <header className="agents-section__head"><h2>Capabilities</h2></header>
        <div className="agents-chip-row">
          <CapabilityChip label="Streaming" on={!!capabilities.streaming} />
          <CapabilityChip label="Push notifications" on={!!capabilities.pushNotifications} />
          <CapabilityChip label="State history" on={!!capabilities.stateTransitionHistory} />
        </div>
      </section>

      {inputModes.length || outputModes.length ? (
        <section className="agents-section">
          <header className="agents-section__head"><h2>Interaction modes</h2></header>
          <div className="agents-card-facts">
            {inputModes.length ? (
              <CardFact label="Input">
                <span className="agents-chip-row">
                  {inputModes.map((mode) => <span key={mode} className="agents-chip agents-chip--info">{mode}</span>)}
                </span>
              </CardFact>
            ) : null}
            {outputModes.length ? (
              <CardFact label="Output">
                <span className="agents-chip-row">
                  {outputModes.map((mode) => <span key={mode} className="agents-chip agents-chip--info">{mode}</span>)}
                </span>
              </CardFact>
            ) : null}
          </div>
        </section>
      ) : null}

      <MiniListSection title="Skills" count={`${skills.length} advertised`}>
        <div className="agents-skill-list">
          {skills.length ? skills.map((skill) => (
            <article key={skill.id || skill.name} className="agents-skill-card">
              <header className="agents-skill-card__head">
                <strong>{skill.name || skill.id}</strong>
                <span className="agents-chip-row">
                  {(skill.tags || []).map((tag) => <span key={tag} className="agents-chip agents-chip--info">{tag}</span>)}
                </span>
              </header>
              {skill.description ? <p className="agents-skill-card__desc">{skill.description}</p> : null}
              {(skill.examples || []).length ? (
                <ul className="agents-skill-card__examples">
                  {skill.examples.map((example) => <li key={example}><code>{example}</code></li>)}
                </ul>
              ) : null}
            </article>
          )) : <PaneEmptyState>No skills advertised.</PaneEmptyState>}
        </div>
      </MiniListSection>

      <MiniListSection title="Recent tasks" count={`${tasks.length} tracked`}>
        <div className="agents-skill-list">
          {tasks.length ? tasks.map((task) => (
            <article key={task.id} className="agents-skill-card">
              <header className="agents-skill-card__head">
                <strong>{task.title || task.id || '(task)'}</strong>
                <span className="agents-chip-row">
                  <span className={`agents-chip agents-chip--${TASK_TONE[task.status] || 'muted'}`}>{task.status}</span>
                  {task.updated ? <span className="agents-skill-card__time">{formatAge(task.updated)}</span> : null}
                </span>
              </header>
              {task.summary ? <p className="agents-skill-card__desc">{task.summary}</p> : null}
              <p className="agents-task-meta">
                {task.source || 'a2a'}{task.context_id ? ` · thread ${task.context_id}` : ''}
              </p>
            </article>
          )) : <PaneEmptyState>No A2A tasks yet.</PaneEmptyState>}
        </div>
      </MiniListSection>
    </div>
  );
}

// Detail pane for ACP agents (Agent Client Protocol over stdio, such as a
// generic Copilot CLI integration). Card-driven like the A2A pane: the
// connection facts, capabilities, and exposed tools come from the agent's ACP
// card, since there is no in-process subagent definition behind it.
export function ACPDetailBody({ agent }) {
  const card = agent.card || {};
  const capabilities = card.capabilities || {};
  const tools = agent.tools || [];
  const runs = agent.tasks || [];

  return (
    <div className="detail-pane__body">
      <div className="agents-metrics">
        <MetricCard label="Transport" value={card.transport || 'stdio'} detail={card.transport === 'tcp' ? 'ACP over TCP' : 'ACP subprocess'} />
        <MetricCard label="Model" value={card.model || agent.model || '—'} detail="Copilot model" />
        <MetricCard label="Tools" value={tools.length} detail="exposed over ACP" />
        <MetricCard label="Protocol" value="ACP" detail="Agent Client Protocol" />
      </div>

      <section className="agents-section">
        <header className="agents-section__head">
          <h2>Connection</h2>
          {card.preview ? <span className="agents-chip agents-chip--info">Preview</span> : null}
        </header>
        <div className="agents-card-facts">
          {agent.summary ? <CardFact label="Description">{agent.summary}</CardFact> : null}
          {card.command ? (
            <CardFact label="Command"><code>{card.command}</code></CardFact>
          ) : null}
          {card.protocol ? <CardFact label="Protocol">{card.protocol}</CardFact> : null}
          {card.permissionMode ? <CardFact label="Permissions">{card.permissionMode}</CardFact> : null}
          {card.timeoutSeconds ? <CardFact label="Timeout">{card.timeoutSeconds}s</CardFact> : null}
        </div>
      </section>

      <section className="agents-section">
        <header className="agents-section__head"><h2>Capabilities</h2></header>
        <div className="agents-chip-row">
          <CapabilityChip label="Streaming" on={!!capabilities.streaming} />
          <CapabilityChip label="Terminal" on={!!capabilities.terminal} />
          <CapabilityChip label="File access" on={!!capabilities.fileAccess} />
          <CapabilityChip label="Auto-approve" on={!!capabilities.autoApprove} />
        </div>
      </section>

      <MiniListSection title="Exposed tools" count={`${tools.length} over ACP`}>
        <div className="agents-connection-list">
          {tools.length ? tools.map((tool) => (
            <ConnectionRow
              key={tool.name}
              icon={(tool.name || '').slice(0, 2)}
              title={tool.name}
              detail={tool.type}
            />
          )) : <PaneEmptyState>No tools exposed.</PaneEmptyState>}
        </div>
      </MiniListSection>

      <MiniListSection title="Recent runs" count={`${runs.length} tracked`}>
        <div className="agents-skill-list">
          {runs.length ? runs.map((run) => (
            <article key={run.id} className="agents-skill-card">
              <header className="agents-skill-card__head">
                <strong>{run.title || run.id || '(run)'}</strong>
                <span className="agents-chip-row">
                  <span className={`agents-chip agents-chip--${TASK_TONE[run.status] || 'muted'}`}>{run.status}</span>
                  {run.updated ? <span className="agents-skill-card__time">{formatAge(run.updated)}</span> : null}
                </span>
              </header>
              {run.summary ? <p className="agents-skill-card__desc">{run.summary}</p> : null}
            </article>
          )) : <PaneEmptyState>No ACP runs yet.</PaneEmptyState>}
        </div>
      </MiniListSection>
    </div>
  );
}

// Detail pane for object agents: represents exactly the YAML front matter +
// Markdown body of the underlying SYSTEM.md (identity, system prompt, tools,
// skills, permissions, interrupt_on) -- no invented metrics/repositories/
// knowledge/activity sections, since object agents have no backing data for
// those. Bundled native manifests are read-only files; user/marketplace
// manifests are editable Postgres documents.
//
// Editing works on the literal SYSTEM.md text (front matter + body), not a
// fixed set of known fields, so an author can add, rename, or remove any
// front-matter key -- the backend persists it verbatim and only rejects it
// if it fails to parse as a valid manifest.
//
// The read-only Manifest section mirrors this: it renders every key present
// in `card.front_matter` (the backend's re-parsed dict of the raw YAML), not
// a hardcoded field list, so custom keys stay visible without opening Edit.
// Keys with their own dedicated section below (tools/skills/permissions/
// interrupt_on) are skipped here to avoid showing the same data twice, as
// is `name`, which is already the page's detail-header title above this
// card.
const FRONT_MATTER_DEDICATED_KEYS = new Set(['name', 'tools', 'skills', 'mounts', 'permissions', 'interrupt_on']);

function humanizeKey(key) {
  return key.replace(/_/g, ' ').replace(/^./, (char) => char.toUpperCase());
}

function formatFrontMatterValue(value) {
  if (value === null || value === undefined || value === '') return '—';
  if (Array.isArray(value)) return value.length ? value.join(', ') : '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

// One skill an object agent's SYSTEM.md `skills:` list references. The bare
// name alone doesn't say much, so this lazily fetches and represents the
// skill's own SKILL.md front matter (the same architecture as SYSTEM.md:
// YAML front matter + a markdown body) when expanded, and edited in place --
// native skills are rewritten on disk, marketplace skills in the document
// store, mirroring ObjectAgentDetailBody below.
function skillSourceFromPath(skillPath) {
  return skillPath.split('/').filter(Boolean).length > 2 ? 'marketplace' : 'native';
}

function AgentSkillRow({ agentId, skillPath, agentsUrl }) {
  const dispatch = useDispatch();
  const skill = useSelector((state) => selectAgentSkillContent(state, skillPath));
  const source = skill?.source || skillSourceFromPath(skillPath);
  const contentKey = entityDraftKey('agent-skill', agentId, `${source}:${skillPath}:content`);
  const expansionKey = entityDraftKey('agent-skill', agentId, `${source}:${skillPath}:expanded`);
  const [draftContent, setDraftContent, draftControls] = useEntityDraft(contentKey, null);
  const [expanded, setExpanded] = useEntityDraft(expansionKey, false);

  const toggle = () => {
    const next = !expanded;
    setExpanded(next);
    if (next && !skill) {
      dispatch(fetchAgentSkillContent({ agentsUrl, skillPath }));
    }
  };

  const startEdit = () => {
    setDraftContent(skill?.raw_content ?? '');
  };

  const cancelEdit = () => {
    draftControls.clear();
  };

  const save = async () => {
    if (draftContent === null || draftControls.saving) return;
    const ticket = draftControls.beginSave();
    if (!ticket) return;
    const operation = dispatch(updateAgentSkillContent({ agentsUrl, skillPath, content: draftContent }));
    draftControls.trackSave(ticket, {
      requestId: operation.requestId,
      events: [UI_EVENT.AGENT_SKILL_CONTENT_UPDATED],
    });
    try {
      await operation.unwrap();
    } catch (saveError) {
      draftControls.failSave(ticket, saveError);
    }
  };

  const frontMatterEntries = skill?.front_matter
    ? Object.entries(skill.front_matter).filter(([key]) => key !== 'name')
    : null;
  const label = skill?.name || skillPath.split('/').filter(Boolean).pop() || skillPath;

  return (
    <article className="agents-skill-card">
      <header className="agents-skill-card__head">
        <button type="button" className="repo-btn repo-btn--ghost" onClick={toggle}>{label}</button>
        {skill ? (
          <span className="agents-chip agents-chip--info">
            {skill.source === 'native' ? 'native' : 'marketplace'}
          </span>
        ) : null}
      </header>
      {!expanded ? null : draftControls.hasDraft ? (
        <div className="agents-draft">
          {draftControls.error ? <p className="agents-draft__note agents-draft__note--error">{draftControls.error}</p> : null}
          <DraftField label="Front matter + body" hint="The literal SKILL.md file: YAML front matter between --- fences, then the markdown body. Any front-matter key can be added, renamed, or removed.">
            <textarea
              rows="14"
              className="agents-json-field"
              value={draftContent ?? ''}
              onChange={(event) => setDraftContent(event.target.value)}
              spellCheck={false}
            />
          </DraftField>
          <div className="agents-graph-editor__actions">
            <button type="button" className="repo-btn repo-btn--ghost" onClick={cancelEdit} disabled={draftControls.saving}>Cancel</button>
            <button type="button" className="repo-btn repo-btn--primary" onClick={save} disabled={draftControls.saving}>{draftControls.saving ? 'Saving…' : 'Save'}</button>
          </div>
        </div>
      ) : skill === undefined ? (
        <p className="agents-skill-card__desc">Loading…</p>
      ) : (
        <div className="agents-card-facts">
          {frontMatterEntries
            ? frontMatterEntries.map(([key, value]) => (
                <CardFact key={key} label={humanizeKey(key)}>{formatFrontMatterValue(value)}</CardFact>
              ))
            : <PaneEmptyState>SKILL.md front matter could not be parsed.</PaneEmptyState>}
          {skill.editable ? (
            <button type="button" className="repo-btn repo-btn--ghost" onClick={startEdit}>Edit</button>
          ) : null}
        </div>
      )}
    </article>
  );
}

export function ObjectAgentDetailBody({
  agent,
  agentsUrl,
  editing = false,
  draft = null,
  onDraftChange,
  onStartEdit,
  onCancelEdit,
  onSaveEdit,
  saving = false,
  error = '',
}) {
  if (editing && agent.card?.source !== 'native') {
    return (
      <div className="detail-pane__body agents-draft">
        <InlineFailure failure={error} operation="Save agent" />
        <section className="agents-section agents-section--prompt">
          <header className="agents-section__head"><h2>SYSTEM.md</h2></header>
          <DraftField label="Front matter + prompt" hint="The literal file: YAML front matter between --- fences, then the markdown system prompt. Any front-matter key can be added, renamed, or removed.">
            <textarea
              rows="24"
              className="agents-json-field"
              value={draft ?? ''}
              onChange={(event) => onDraftChange(event.target.value)}
              spellCheck={false}
            />
          </DraftField>
        </section>
        <div className="agents-graph-editor__actions">
          <button type="button" className="repo-btn repo-btn--ghost" onClick={onCancelEdit} disabled={saving}>Cancel</button>
          <button type="button" className="repo-btn repo-btn--primary" onClick={onSaveEdit} disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
        </div>
      </div>
    );
  }

  const tools = agent.tools || [];
  const skills = agent.skills || [];
  const permissions = agent.card?.permissions || [];
  const interruptOn = agent.card?.interrupt_on || {};
  const frontMatter = agent.card?.front_matter;
  const frontMatterEntries = frontMatter
    ? Object.entries(frontMatter).filter(([key]) => !FRONT_MATTER_DEDICATED_KEYS.has(key))
    : null;

  const isObjectAgent = agent.kind === 'object';

  return (
    <div className="detail-pane__body">
      {isObjectAgent ? (
        <section className="agents-section">
          <header className="agents-section__head">
            <h2>Manifest</h2>
            {agent.card?.source !== 'native' ? (
              <button type="button" className="repo-btn repo-btn--ghost" onClick={onStartEdit}>Edit</button>
            ) : <span>Read-only · bundled file</span>}
          </header>
          <div className="agents-card-facts">
            {frontMatterEntries
              ? frontMatterEntries.map(([key, value]) => (
                  <CardFact key={key} label={humanizeKey(key)}>{formatFrontMatterValue(value)}</CardFact>
                ))
              : <PaneEmptyState>Manifest front matter could not be parsed.</PaneEmptyState>}
          </div>
        </section>
      ) : null}

      <section className="agents-section agents-section--prompt">
        <header className="agents-section__head">
          <h2>System prompt <span>{(agent.prompt || '').length} chars</span></h2>
        </header>
        <pre className="agents-prompt"><code>{agent.prompt || 'No system prompt set.'}</code></pre>
      </section>

      <MiniListSection title="Tools" count={`${tools.length} attached`}>
        <div className="agents-chip-row">
          {tools.length
            ? tools.map((tool) => <span key={tool.name} className="agents-chip agents-chip--info">{tool.name}</span>)
            : <PaneEmptyState>No tools attached.</PaneEmptyState>}
        </div>
      </MiniListSection>

      <MiniListSection title="Skills" count={`${skills.length} attached`}>
        <div className="agents-skill-list">
          {skills.length
            ? skills.map((skill) => {
                const skillPath = skill.name || skill;
                return <AgentSkillRow key={skillPath} agentId={agent.id} skillPath={skillPath} agentsUrl={agentsUrl} />;
              })
            : <PaneEmptyState>No skills attached.</PaneEmptyState>}
        </div>
      </MiniListSection>

      {permissions.length ? (
        <section className="agents-section">
          <header className="agents-section__head"><h2>Permissions</h2></header>
          <div className="agents-card-facts">
            {permissions.map((perm, index) => (
              <CardFact key={`${perm.mode || 'allow'}-${index}`} label={perm.mode || 'allow'}>
                {(perm.operations || []).join('/')} — {(perm.paths || []).join(', ')}
              </CardFact>
            ))}
          </div>
        </section>
      ) : null}

      {Object.keys(interruptOn).length ? (
        <section className="agents-section">
          <header className="agents-section__head"><h2>Interrupt on</h2></header>
          <pre className="agents-prompt"><code>{JSON.stringify(interruptOn, null, 2)}</code></pre>
        </section>
      ) : null}
    </div>
  );
}
