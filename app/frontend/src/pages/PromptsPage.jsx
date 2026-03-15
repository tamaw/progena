import { useEffect, useMemo, useState } from 'react';
import Header from '../components/layout/Header';
import { useApi } from '../hooks/useApi';

function buildPromptTree(prompts) {
  const groups = {};

  prompts.forEach((prompt) => {
    const agentKey = prompt.primary_agent || prompt.category || 'unassigned';
    if (!groups[agentKey]) {
      groups[agentKey] = {
        prompts: [],
        children: {},
      };
    }

    const parts = prompt.path.split('/');
    const promptName = parts.pop();
    let cursor = groups[agentKey];

    parts.forEach((segment, index) => {
      if (index === 0 && segment === agentKey) {
        return;
      }

      if (!cursor.children[segment]) {
        cursor.children[segment] = {
          prompts: [],
          children: {},
        };
      }
      cursor = cursor.children[segment];
    });

    cursor.prompts.push({
      ...prompt,
      displayName: promptName || prompt.filename,
    });
  });

  return Object.entries(groups)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([name, node]) => ({ name, ...node }));
}

function sortTreeNode(node) {
  return {
    ...node,
    prompts: [...node.prompts].sort((left, right) => left.displayName.localeCompare(right.displayName)),
    children: Object.entries(node.children)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([name, child]) => ({
        name,
        ...sortTreeNode(child),
      })),
  };
}

function PromptTreeBranch({
  label,
  branchKey,
  node,
  depth = 0,
  selectedPath,
  expandedNodes,
  onToggle,
  onSelectPrompt,
}) {
  const isExpanded = expandedNodes[branchKey] ?? depth === 0;

  return (
    <div className="prompts-tree-branch">
      <button
        className={`prompts-tree-toggle depth-${Math.min(depth, 3)} ${isExpanded ? 'expanded' : ''}`}
        onClick={() => onToggle(branchKey)}
        type="button"
      >
        <span className="prompts-tree-caret">{isExpanded ? '▾' : '▸'}</span>
        <span className="prompts-tree-label">{label}</span>
      </button>

      {isExpanded && (
        <div className="prompts-tree-children">
          {node.children.map((child) => (
            <PromptTreeBranch
              key={`${branchKey}/${child.name}`}
              label={child.name}
              branchKey={`${branchKey}/${child.name}`}
              node={child}
              depth={depth + 1}
              selectedPath={selectedPath}
              expandedNodes={expandedNodes}
              onToggle={onToggle}
              onSelectPrompt={onSelectPrompt}
            />
          ))}

          {node.prompts.map((prompt) => (
            <button
              key={prompt.path}
              className={`prompts-tree-leaf depth-${Math.min(depth + 1, 3)} ${selectedPath === prompt.path ? 'active' : ''}`}
              onClick={() => onSelectPrompt(prompt)}
              type="button"
              title={prompt.path}
            >
              <span className="prompts-tree-file">{prompt.displayName}</span>
              <span className="prompts-tree-file-path">{prompt.path}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function PromptsPage() {
  const { connectionState, getPrompts, updatePromptContent } = useApi();
  const [prompts, setPrompts] = useState([]);
  const [selectedPath, setSelectedPath] = useState('');
  const [draftContent, setDraftContent] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [statusMessage, setStatusMessage] = useState('');
  const [expandedNodes, setExpandedNodes] = useState({});

  useEffect(() => {
    let active = true;

    async function loadPrompts() {
      setLoading(true);
      setError(null);
      try {
        const response = await getPrompts();
        if (!active) {
          return;
        }
        const items = response.prompts || [];
        setPrompts(items);
        setSelectedPath((currentSelected) => {
          const nextSelected = items.find((prompt) => prompt.path === currentSelected)?.path || items[0]?.path || '';
          const nextPrompt = items.find((prompt) => prompt.path === nextSelected);
          setDraftContent(nextPrompt?.content || '');
          return nextSelected;
        });
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

  const promptTree = useMemo(
    () => buildPromptTree(prompts).map((group) => ({ ...group, ...sortTreeNode(group) })),
    [prompts]
  );
  const selectedPrompt = useMemo(
    () => prompts.find((prompt) => prompt.path === selectedPath) || null,
    [prompts, selectedPath]
  );
  const hasUnsavedChanges = selectedPrompt ? draftContent !== (selectedPrompt.content || '') : false;

  const toggleBranch = (branchKey) => {
    setExpandedNodes((current) => ({
      ...current,
      [branchKey]: !(current[branchKey] ?? true),
    }));
  };

  const handleSelectPrompt = (prompt) => {
    setError(null);
    setSelectedPath(prompt.path);
    setDraftContent(prompt.content || '');
    setStatusMessage('');
  };

  const handleReload = async () => {
    if (!selectedPrompt) {
      return;
    }
    setError(null);
    setDraftContent(selectedPrompt.content || '');
    setStatusMessage('Reverted unsaved changes.');
  };

  const handleSave = async () => {
    if (!selectedPrompt || !hasUnsavedChanges) {
      return;
    }
    setSaving(true);
    setError(null);
    setStatusMessage('');
    try {
      const updatedPrompt = await updatePromptContent(selectedPrompt.path, draftContent);
      setPrompts((current) => current.map((prompt) => (
        prompt.path === updatedPrompt.path ? updatedPrompt : prompt
      )));
      setDraftContent(updatedPrompt.content || '');
      setStatusMessage(`Saved ${updatedPrompt.path}.`);
    } catch (saveError) {
      setError(saveError);
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <Header connectionState={connectionState} />
      <div className="prompts-page">
        <aside className="prompts-sidebar">
          <div className="prompts-sidebar-header">
            <h2>Prompt Editor</h2>
            <span>{prompts.length}</span>
          </div>
          <div className="prompts-sidebar-body">
            {loading && <div className="prompts-empty-state">Loading prompts...</div>}
            {error && !loading && prompts.length === 0 && <div className="prompts-empty-state">Failed to load prompts.</div>}
            {!loading && prompts.length === 0 && (
              <div className="prompts-empty-state">No prompts found.</div>
            )}
            {!loading && promptTree.map((group) => (
              <PromptTreeBranch
                key={group.name}
                label={group.name}
                branchKey={group.name}
                node={group}
                selectedPath={selectedPath}
                expandedNodes={expandedNodes}
                onToggle={toggleBranch}
                onSelectPrompt={handleSelectPrompt}
              />
            ))}
          </div>
        </aside>

        <main className="prompts-editor">
          {!selectedPrompt && !loading && (
            <div className="prompts-empty-state">Select a prompt to edit.</div>
          )}

          {selectedPrompt && (
            <>
              <div className="prompts-editor-header">
                <div>
                  <h2>{selectedPrompt.filename}</h2>
                  <div className="prompts-editor-path">{selectedPrompt.path}</div>
                  {selectedPrompt.associated_agents?.length > 0 && (
                    <div className="prompts-editor-badges">
                      {selectedPrompt.associated_agents.map((agent) => (
                        <span key={`${selectedPrompt.path}:${agent}`} className="prompts-editor-badge">
                          {agent}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                <div className="prompts-editor-actions">
                  <button className="node-action-btn" onClick={handleReload} disabled={!hasUnsavedChanges || saving}>
                    Revert
                  </button>
                  <button className="node-action-btn primary" onClick={handleSave} disabled={!hasUnsavedChanges || saving}>
                    {saving ? 'Saving...' : 'Save'}
                  </button>
                </div>
              </div>

              <textarea
                className="prompts-editor-textarea"
                value={draftContent}
                onChange={(event) => setDraftContent(event.target.value)}
                spellCheck={false}
              />

              <div className="prompts-editor-footer">
                <span>{hasUnsavedChanges ? 'Unsaved changes' : 'Saved'}</span>
                <span>{statusMessage || error?.message || ''}</span>
              </div>
            </>
          )}
        </main>
      </div>
    </>
  );
}
