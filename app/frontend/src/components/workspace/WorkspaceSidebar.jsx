import { memo, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApi } from '../../hooks/useApi';
import { useWorkspaceActions, useWorkspaceMeta } from '../../context/WorkspaceContext';

const typeLabels = {
  prompt: 'Prompt',
  code: 'Code',
  user: 'User',
  webframe: 'Web Frame',
  label: 'Label',
  schedule: 'Schedule',
  event: 'Event',
  group: 'Group',
  agent: 'Agent',
  mcp: 'MCP',
};

function normalizeAgentName(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
}

function formatLabel(value) {
  return String(value || '')
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function formatFieldValue(value) {
  if (Array.isArray(value)) {
    return value.length > 0 ? value.join(', ') : 'None';
  }

  if (typeof value === 'boolean') {
    return value ? 'Yes' : 'No';
  }

  return String(value || '');
}

function buildAgentAliases(value) {
  const normalized = normalizeAgentName(value);
  const aliases = new Set([normalized]);

  if (normalized.endsWith('copilot')) {
    aliases.add(`${normalized.slice(0, -'copilot'.length)}agent`);
  }
  if (normalized.endsWith('agent')) {
    aliases.add(`${normalized.slice(0, -'agent'.length)}copilot`);
  }
  if (normalized === 'progenitor') {
    aliases.add('builderprogenitor');
  }
  if (normalized === 'builderprogenitor') {
    aliases.add('progenitor');
  }

  return aliases;
}

function getPromptAgentCandidates(prompt) {
  return [
    prompt?.primary_agent,
    prompt?.category,
    ...(prompt?.associated_agents || []),
  ];
}

function matchesPromptToAliases(prompt, aliases) {
  const promptAliases = new Set();
  getPromptAgentCandidates(prompt).forEach((value) => {
    buildAgentAliases(value).forEach((alias) => promptAliases.add(alias));
  });
  return [...promptAliases].some((alias) => aliases.has(alias));
}

function WorkspaceSidebar({
  collapsed,
  onToggle,
  disabled = false,
  selectedNode = null,
  width = null,
}) {
  const { getPrompts } = useApi();
  const { updateNodeData, connectPromptToAgent } = useWorkspaceActions();
  const { agentOptions, getWidgetSchema } = useWorkspaceMeta();
  const navigate = useNavigate();
  const [prompts, setPrompts] = useState([]);
  const [loadingPrompts, setLoadingPrompts] = useState(false);
  const [promptError, setPromptError] = useState(null);
  const [collapsedSections, setCollapsedSections] = useState({});
  const [activeModalField, setActiveModalField] = useState(null);
  const detailsScrollRef = useRef(null);
  const selectedNodeIdRef = useRef(selectedNode?.id || null);
  const lastPromptRequestKeyRef = useRef(null);

  const activeSchema = selectedNode ? getWidgetSchema(selectedNode.type) || null : null;

  useEffect(() => {
    const nextSelectedId = selectedNode?.id || null;
    if (selectedNodeIdRef.current !== nextSelectedId) {
      selectedNodeIdRef.current = nextSelectedId;
      if (detailsScrollRef.current) {
        detailsScrollRef.current.scrollTop = 0;
      }
    }
  }, [selectedNode?.id, selectedNode?.type]);

  useEffect(() => {
    setActiveModalField(null);
  }, [selectedNode?.id]);

  useEffect(() => {
    if (!activeModalField) {
      return undefined;
    }

    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        setActiveModalField(null);
      }
    };

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [activeModalField]);

  useEffect(() => {
    if (!activeSchema) {
      setCollapsedSections({});
      return;
    }

    setCollapsedSections(
      Object.fromEntries(
        (activeSchema.sections || []).map((section) => [section.id, section.default_expanded === false])
      )
    );
  }, [activeSchema, selectedNode?.id, selectedNode?.type]);

  const selectedNodeType = selectedNode?.type || null;
  const selectedNodeId = selectedNode?.id || null;
  const selectedNodeAgentId = selectedNode?.data?.agentId || '';
  const selectedNodeTitleValue = selectedNode?.data?.title || '';

  const promptLookupNames = useMemo(() => {
    if (selectedNodeType !== 'agent') {
      return [];
    }

    const aliases = new Set();
    [selectedNodeAgentId, selectedNodeTitleValue].forEach((value) => {
      buildAgentAliases(value).forEach((alias) => aliases.add(alias));
    });
    return [...aliases].filter(Boolean);
  }, [
    selectedNodeAgentId,
    selectedNodeTitleValue,
    selectedNodeType,
  ]);

  const promptLookupKey = useMemo(
    () => promptLookupNames.join('|'),
    [promptLookupNames]
  );

  useEffect(() => {
    let active = true;
    const requestKey = `${selectedNodeId || 'none'}::${selectedNodeType || 'none'}::${promptLookupKey}`;
    const lookupNames = promptLookupKey ? promptLookupKey.split('|').filter(Boolean) : [];

    async function loadPrompts() {
      if (!selectedNodeId || selectedNodeType !== 'agent' || lookupNames.length === 0) {
        lastPromptRequestKeyRef.current = null;
        setPrompts([]);
        setPromptError(null);
        setLoadingPrompts(false);
        return;
      }

      if (lastPromptRequestKeyRef.current === requestKey) {
        return;
      }

      lastPromptRequestKeyRef.current = requestKey;

      setLoadingPrompts(true);
      setPromptError(null);

      try {
        const response = await getPrompts();
        if (!active) {
          return;
        }

        const lookupAliases = new Set(lookupNames);
        const allPrompts = response.prompts || [];
        const matchedPrompts = allPrompts.filter((prompt) => matchesPromptToAliases(prompt, lookupAliases));
        setPrompts(matchedPrompts);
      } catch (error) {
        if (active) {
          lastPromptRequestKeyRef.current = null;
          setPromptError(error);
        }
      } finally {
        if (active) {
          setLoadingPrompts(false);
        }
      }
    }

    loadPrompts();
    return () => {
      active = false;
    };
  }, [getPrompts, promptLookupKey, selectedNodeId, selectedNodeType]);

  const onFieldChange = (fieldKey, nextValue) => {
    if (!selectedNode) {
      return;
    }

    if (fieldKey === 'targetAgent') {
      connectPromptToAgent(selectedNode.id, nextValue);
      return;
    }

    updateNodeData(selectedNode.id, { [fieldKey]: nextValue });
  };

  const selectedNodeTitle = formatLabel(selectedNode?.data?.title || selectedNode?.id || 'Untitled');
  const isCompactField = (field) => field.control === 'textarea';

  const renderFieldControl = (field) => {
    const disabledField = disabled || !field.editable;

    if (!field.editable) {
      const value = selectedNode?.data?.[field.key];
      return <div className="sidebar-property-value readonly">{formatFieldValue(value)}</div>;
    }

    if (field.control === 'textarea') {
      return (
        <div className="sidebar-modal-field compact">
          <button
            className="sidebar-property-modal-trigger"
            type="button"
            onClick={() => setActiveModalField(field)}
            disabled={disabledField}
          >
            Edit
          </button>
        </div>
      );
    }

    const value = selectedNode?.data?.[field.key];

    if (field.control === 'boolean') {
      return (
        <label className="sidebar-checkbox">
          <input
            type="checkbox"
            checked={Boolean(value)}
            onChange={(event) => onFieldChange(field.key, event.target.checked)}
            disabled={disabledField}
          />
          <span>{value ? 'Enabled' : 'Disabled'}</span>
        </label>
      );
    }

    if (field.control === 'select') {
      const options = field.option_source === 'agents'
        ? agentOptions.map((option) => ({ value: option, label: formatLabel(option) || option }))
        : field.options || [];

      return (
        <select
          className="sidebar-property-input"
          value={value || ''}
          onChange={(event) => onFieldChange(field.key, event.target.value)}
          disabled={disabledField}
        >
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      );
    }

    return (
      <input
        className="sidebar-property-input"
        value={value || ''}
        onChange={(event) => onFieldChange(field.key, event.target.value)}
        disabled={disabledField}
      />
    );
  };

  return (
    <>
      <aside
        className={`workspace-sidebar ${collapsed ? 'collapsed' : ''}`}
        style={!collapsed && width ? { width: `${width}px`, minWidth: `${width}px` } : undefined}
      >
        <div className="sidebar-header">
          <h3>Details</h3>
          <button
            className="sidebar-toggle"
            onClick={onToggle}
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            type="button"
          >
            {collapsed ? '\u25B6' : '\u25C0'}
          </button>
        </div>

        {!selectedNode ? (
          <div className="sidebar-details sidebar-details-empty">
            <div className="sidebar-details-section">
              <div className="sidebar-details-title">No Selection</div>
              <div className="sidebar-details-text">
                Click a node to inspect and edit its properties. Drag new elements in from the dock at the bottom.
              </div>
            </div>
          </div>
        ) : (
            <div
              ref={detailsScrollRef}
              className="sidebar-details"
            >
            <div className="sidebar-selection-summary">
              <h2 className="sidebar-selection-title" title={selectedNodeTitle}>{selectedNodeTitle}</h2>
              <span className="sidebar-selection-type">{typeLabels[selectedNode.type] || selectedNode.type}</span>
            </div>
            {(activeSchema?.sections || []).map((section) => {
              const isCollapsed = collapsedSections[section.id] ?? false;

              return (
                <div key={section.id} className="sidebar-details-section sidebar-card">
                  <button
                    className="sidebar-section-toggle"
                    type="button"
                    onClick={() => setCollapsedSections((current) => ({ ...current, [section.id]: !isCollapsed }))}
                  >
                    <span className="sidebar-section-label">{section.label}</span>
                    <span>{isCollapsed ? '\u25BC' : '\u25B2'}</span>
                  </button>

                  {!isCollapsed && (
                    <div className="sidebar-properties">
                      {section.fields.map((field) => (
                        <div
                          key={field.key}
                          className={`sidebar-property-row ${isCompactField(field) ? 'compact' : ''}`}
                        >
                          <span className="sidebar-property-label">{field.label}</span>
                          {renderFieldControl(field)}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}

            {!!selectedNode.data?.summaryState && (
              <div className="sidebar-details-section sidebar-card">
                <button
                  className="sidebar-section-toggle"
                  type="button"
                  onClick={() => setCollapsedSections((current) => ({ ...current, output: !current.output }))}
                >
                  <span className="sidebar-section-label">Latest Output</span>
                  <span>{collapsedSections.output ? '\u25BC' : '\u25B2'}</span>
                </button>
                {!collapsedSections.output && (
                  <div className="sidebar-details-text">{selectedNode.data.summaryState}</div>
                )}
              </div>
            )}

            {selectedNode.type === 'agent' && (
              <div className="sidebar-details-section sidebar-card">
                <button
                  className="sidebar-section-toggle"
                  type="button"
                  onClick={() => setCollapsedSections((current) => ({ ...current, prompts: !current.prompts }))}
                >
                  <span className="sidebar-section-label">Related Prompts</span>
                  <span>{collapsedSections.prompts ? '\u25BC' : '\u25B2'}</span>
                </button>

                {!collapsedSections.prompts && (
                  <>
                    {loadingPrompts && <div className="prompt-library-empty">Loading prompts...</div>}
                    {promptError && !loadingPrompts && <div className="prompt-library-empty">Failed to load prompts.</div>}
                    {!loadingPrompts && !promptError && prompts.length === 0 && (
                      <div className="prompt-library-empty">No prompts found.</div>
                    )}
                    {!loadingPrompts && !promptError && prompts.length > 0 && (
                      <div className="prompt-library-list">
                        {prompts.map((prompt) => (
                          <button
                            key={prompt.path}
                            className="prompt-library-list-item"
                            disabled={disabled}
                            onClick={() => navigate(`/prompts?path=${encodeURIComponent(prompt.path)}`)}
                            type="button"
                            title={prompt.path}
                          >
                            <span className="prompt-library-list-name">{prompt.filename}</span>
                            <span className="prompt-library-list-action">Edit</span>
                          </button>
                        ))}
                      </div>
                    )}
                  </>
                )}
              </div>
            )}
          </div>
        )}
      </aside>

      {activeModalField && selectedNode && !collapsed && (
        <div className="sidebar-modal-backdrop" onClick={() => setActiveModalField(null)} role="presentation">
          <div className="sidebar-modal" onClick={(event) => event.stopPropagation()} role="dialog" aria-modal="true">
            <div className="sidebar-modal-header">
              <div>
                <div className="sidebar-details-title">{activeModalField.label}</div>
                <div className="sidebar-modal-title">{selectedNodeTitle}</div>
              </div>
              <button
                className="sidebar-toggle"
                type="button"
                onClick={() => setActiveModalField(null)}
                aria-label="Close content editor"
              >
                ×
              </button>
            </div>
            <textarea
              className="sidebar-modal-textarea"
              value={selectedNode.data?.[activeModalField.key] || ''}
              onChange={(event) => onFieldChange(activeModalField.key, event.target.value)}
              disabled={disabled}
              spellCheck={false}
              autoFocus
            />
          </div>
        </div>
      )}
    </>
  );
}

export default memo(WorkspaceSidebar);
