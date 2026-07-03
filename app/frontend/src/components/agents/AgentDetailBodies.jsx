import { MetricCard, CardFact, CapabilityChip } from '../common/DetailCards';
import { formatAge } from './agentFormat';

// Chip tone per A2A task status (matches Progenitor's A2ATaskStatus enum).
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
          )) : <div className="agents-empty-state">No skills advertised.</div>}
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
          )) : <div className="agents-empty-state">No A2A tasks yet.</div>}
        </div>
      </MiniListSection>
    </div>
  );
}

// Detail pane for ACP agents (Agent Client Protocol over stdio, e.g. the GitHub
// Copilot CLI the Builder Agent drives). Card-driven like the A2A pane: the
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
              detail={`${tool.type} - ${tool.calls} calls`}
              meta={tool.enabled ? 'on' : 'off'}
              tone={tool.enabled ? 'ok' : 'muted'}
            />
          )) : <div className="agents-empty-state">No tools exposed.</div>}
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
          )) : <div className="agents-empty-state">No ACP runs yet.</div>}
        </div>
      </MiniListSection>
    </div>
  );
}

// Detail pane for DeepAgent subagents: definition-centric (system prompt, tools,
// repositories, knowledge, and activity), reflecting their in-process nature.
export function SubagentDetailBody({ agent }) {
  return (
    <div className="detail-pane__body">
      <div className="agents-metrics">
        <MetricCard label="Invocations" value={agent.metrics?.invocations ?? '0'} detail="last 30 days" />
        <MetricCard label="Sessions" value={agent.metrics?.sessions ?? '0'} detail="linked conversations" />
        <MetricCard label="Tasks" value={agent.metrics?.tasks ?? '0'} detail="open and completed" />
        <MetricCard label="Memories" value={agent.metrics?.memories ?? '0'} detail="available context" />
      </div>

      <section className="agents-section agents-section--prompt">
        <header className="agents-section__head">
          <h2>System prompt <span>{(agent.prompt || '').length} chars</span></h2>
          <div>
            <button type="button" className="repo-btn repo-btn--ghost">Edit</button>
            <button type="button" className="repo-btn repo-btn--ghost">Diff</button>
          </div>
        </header>
        <pre className="agents-prompt"><code>{agent.prompt || 'No system prompt set.'}</code></pre>
      </section>

      <div className="agents-detail-grid">
        <MiniListSection title="Connected tools" count={`${(agent.tools || []).length} attached`} action="+ Add tool">
          <div className="agents-connection-list">
            {(agent.tools || []).length ? agent.tools.map((tool) => (
              <ConnectionRow
                key={tool.name}
                icon={(tool.name || '').slice(0, 2)}
                title={tool.name}
                detail={`${tool.type} - ${tool.calls} calls`}
                meta={tool.enabled ? 'on' : 'off'}
                tone={tool.enabled ? 'ok' : 'muted'}
              />
            )) : <div className="agents-empty-state">No tools connected.</div>}
          </div>
        </MiniListSection>

        <MiniListSection title="Repositories" count={`${(agent.repositories || []).length} connected`} action="+ Attach repo">
          <div className="agents-connection-list">
            {(agent.repositories || []).length ? agent.repositories.map((repo) => (
              <ConnectionRow
                key={`${repo.name}-${repo.branch}`}
                icon="repo"
                title={repo.name}
                detail={`branch ${repo.branch}`}
                meta={repo.access}
                tone={repo.access === 'write' ? 'ok' : 'info'}
              />
            )) : <div className="agents-empty-state">No repositories connected.</div>}
          </div>
        </MiniListSection>

        <MiniListSection title="Knowledge" count={`${(agent.knowledge || []).length} sources`} action="+ Link source">
          <div className="agents-connection-list">
            {(agent.knowledge || []).length ? agent.knowledge.map((item) => (
              <ConnectionRow
                key={item.name}
                icon="kb"
                title={item.name}
                detail={`${item.records} records`}
                meta={item.freshness}
                tone="info"
              />
            )) : <div className="agents-empty-state">No knowledge linked.</div>}
          </div>
        </MiniListSection>
      </div>

      <div className="agents-activity-grid">
        <MiniListSection title="Memories" count={`${(agent.memories || []).length} pinned`}>
          <div className="agents-mini-list">
            {(agent.memories || []).length ? agent.memories.map((memory) => (
              <div key={memory.title} className="agents-mini-row">
                <span className="agents-mini-row__icon">mem</span>
                <div>
                  <strong>{memory.title}</strong>
                  <p>{memory.scope} - {memory.age}</p>
                </div>
              </div>
            )) : <div className="agents-empty-state">No memories yet.</div>}
          </div>
        </MiniListSection>

        <MiniListSection title="Sessions" count={`${(agent.sessions || []).length} recent`}>
          <div className="agents-mini-list">
            {(agent.sessions || []).length ? agent.sessions.map((session) => (
              <div key={session.title} className="agents-mini-row">
                <span className="agents-mini-row__icon">ses</span>
                <div>
                  <strong>{session.title}</strong>
                  <p>{session.outcome} - {session.age}</p>
                </div>
              </div>
            )) : <div className="agents-empty-state">No sessions yet.</div>}
          </div>
        </MiniListSection>

        <MiniListSection title="Tasks" count={`${(agent.tasks || []).length} tracked`}>
          <div className="agents-mini-list">
            {(agent.tasks || []).length ? agent.tasks.map((task) => (
              <div key={task.title} className="agents-mini-row">
                <span className="agents-mini-row__icon">tsk</span>
                <div>
                  <strong>{task.title}</strong>
                  <p>{task.state} - {task.age}</p>
                </div>
                <span className={`agents-chip agents-chip--${task.state === 'active' ? 'ok' : 'info'}`}>{task.state}</span>
              </div>
            )) : <div className="agents-empty-state">No tasks tracked.</div>}
          </div>
        </MiniListSection>
      </div>
    </div>
  );
}
