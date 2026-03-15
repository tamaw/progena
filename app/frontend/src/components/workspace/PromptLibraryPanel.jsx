import { memo, useEffect, useMemo, useState } from 'react';
import { useApi } from '../../hooks/useApi';
import { useWorkspaceContext } from '../../context/WorkspaceContext';

function groupPrompts(prompts) {
  return prompts.reduce((groups, prompt) => {
    const key = prompt.primary_agent || prompt.category || 'unassigned';
    if (!groups[key]) {
      groups[key] = [];
    }
    groups[key].push(prompt);
    return groups;
  }, {});
}

function PromptLibraryPanel({ disabled = false }) {
  const { getPrompts } = useApi();
  const { addPromptFromLibrary } = useWorkspaceContext();
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
  }, [getPrompts]);

  const groupedPrompts = useMemo(() => groupPrompts(prompts), [prompts]);

  return (
    <aside className={`prompt-library-panel ${disabled ? 'disabled' : ''}`}>
      <div className="prompt-library-header">
        <h3>Prompt Library</h3>
        <span className="prompt-library-count">{prompts.length}</span>
      </div>

      <div className="prompt-library-body">
        {loading && <div className="prompt-library-empty">Loading prompts...</div>}
        {error && !loading && <div className="prompt-library-empty">Failed to load prompts.</div>}
        {!loading && !error && prompts.length === 0 && (
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
                      onClick={() => addPromptFromLibrary(prompt)}
                    >
                      Add
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
      </div>
    </aside>
  );
}

export default memo(PromptLibraryPanel);
