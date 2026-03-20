import { memo, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApi } from '../../hooks/useApi';

function groupPrompts(prompts, selectedAgent) {
  return prompts.reduce((groups, prompt) => {
    const key = selectedAgent ? (prompt.directory || 'root') : (prompt.primary_agent || prompt.category || 'unassigned');
    if (!groups[key]) {
      groups[key] = [];
    }
    groups[key].push(prompt);
    return groups;
  }, {});
}

function normalizeAgentName(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
}

function matchesAgent(prompt, selectedAgent) {
  const target = normalizeAgentName(selectedAgent);
  if (!target) {
    return false;
  }

  return [
    prompt.primary_agent,
    prompt.category,
    ...(prompt.associated_agents || []),
  ].some((value) => normalizeAgentName(value) === target);
}

function PromptLibraryPanel({
  disabled = false,
  collapsed = false,
  onToggle,
  selectedAgent = '',
}) {
  const { getPrompts } = useApi();
  const navigate = useNavigate();
  const [prompts, setPrompts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let active = true;

    async function loadPrompts() {
      setLoading(true);
      setError(null);
      try {
        const response = await getPrompts();
        if (active) {
          setPrompts(response.prompts || []);
        }
      } catch (loadError) {
        if (active) {
          setError(loadError);
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    loadPrompts();
    return () => {
      active = false;
    };
  }, [getPrompts, selectedAgent]);

  const filteredPrompts = useMemo(
    () => (selectedAgent ? prompts.filter((prompt) => matchesAgent(prompt, selectedAgent)) : []),
    [prompts, selectedAgent]
  );
  const groupedPrompts = useMemo(
    () => groupPrompts(filteredPrompts, selectedAgent),
    [filteredPrompts, selectedAgent]
  );

  return (
    <aside className={`prompt-library-panel ${collapsed ? 'collapsed' : ''} ${disabled ? 'disabled' : ''}`}>
      <div className="prompt-library-header">
        {!collapsed && (
          <>
            <h3>{selectedAgent ? 'Related Prompts' : 'Prompt Library'}</h3>
            <span className="prompt-library-count">{filteredPrompts.length}</span>
          </>
        )}
        <button
          className="prompt-library-toggle"
          onClick={onToggle}
          title={collapsed ? 'Expand prompts panel' : 'Collapse prompts panel'}
          type="button"
        >
          {collapsed ? '\u25C0' : '\u25B6'}
        </button>
      </div>

      {!collapsed && <div className="prompt-library-body">
        {!selectedAgent && !loading && (
          <div className="prompt-library-empty">Select an agent node to view related prompts.</div>
        )}
        {loading && <div className="prompt-library-empty">Loading prompts...</div>}
        {error && !loading && <div className="prompt-library-empty">Failed to load prompts.</div>}
        {!loading && !error && selectedAgent && filteredPrompts.length === 0 && (
          <div className="prompt-library-empty">No prompts found.</div>
        )}

        {!loading && !error && Object.entries(groupedPrompts).map(([groupName, groupPrompts]) => (
          <section key={groupName} className="prompt-library-group">
            <div className="prompt-library-group-title">{groupName}</div>
            <div className="prompt-library-items">
              {groupPrompts.map((prompt) => (
                <div key={prompt.path} className="prompt-library-item">
                  <div className="prompt-library-item-header">
                    <div className="prompt-library-item-title">{prompt.filename}</div>
                    <button
                      className="node-action-btn primary"
                      disabled={disabled}
                      onClick={() => navigate(`/prompts?path=${encodeURIComponent(prompt.path)}`)}
                    >
                      Edit
                    </button>
                  </div>
                  <div className="prompt-library-item-path">{prompt.path}</div>
                  {prompt.associated_agents?.length > 0 && (
                    <div className="prompt-library-badges">
                      {prompt.associated_agents.map((agent) => (
                        <span key={`${prompt.path}:${agent}`} className="prompt-library-badge">
                          {agent}
                        </span>
                      ))}
                    </div>
                  )}
                  <div className="prompt-library-preview">{prompt.preview || 'Empty prompt'}</div>
                </div>
              ))}
            </div>
          </section>
        ))}
      </div>}
    </aside>
  );
}

export default memo(PromptLibraryPanel);
