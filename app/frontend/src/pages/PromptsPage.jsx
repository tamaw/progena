import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useApi } from '../hooks/useApi';
import { renderMarkdownToHtml } from '../lib/markdown';
import {
  PaneCollapseSeam,
  PaneExpandRail,
  PaneListFilters,
  PaneListGroup,
  PaneListScroll,
  PaneListSearch,
  PaneListToolbar,
} from '../components/layout/PaneList';
import { usePaneCollapse } from '../components/layout/usePaneCollapse';
import { PaneSection } from '../components/layout/PaneList';

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
  hideLabel = false,
  selectedPath,
  expandedNodes,
  onToggle,
  onSelectPrompt,
}) {
  const isExpanded = hideLabel ? true : (expandedNodes[branchKey] ?? depth === 0);

  return (
    <div className="prompts-tree-branch">
      {!hideLabel && (
        <button
          className={`prompts-tree-toggle depth-${Math.min(depth, 3)} ${isExpanded ? 'expanded' : ''}`}
          onClick={() => onToggle(branchKey)}
          type="button"
        >
          <span className="prompts-tree-caret">{isExpanded ? '▾' : '▸'}</span>
          <span className="prompts-tree-label pane-row-caption">{label}</span>
        </button>
      )}

      {isExpanded && (
        <div className="prompts-tree-children">
          {node.children.map((child) => (
            <PromptTreeBranch
              key={`${branchKey}/${child.name}`}
              label={child.name}
              branchKey={`${branchKey}/${child.name}`}
              node={child}
              depth={depth + 1}
              hideLabel={false}
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
              <span className="prompts-tree-file pane-row-title">{prompt.displayName}</span>
              <span className="prompts-tree-file-path pane-row-caption">{prompt.path}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function PromptsPage() {
  const { getPrompts, updatePromptContent } = useApi();
  const { collapsed, setCollapsed, paneClassName } = usePaneCollapse('prompts-sidebar sess-list');
  const [searchParams, setSearchParams] = useSearchParams();
  const [prompts, setPrompts] = useState([]);
  const [selectedPath, setSelectedPath] = useState('');
  const [draftContent, setDraftContent] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [statusMessage, setStatusMessage] = useState('');
  const [expandedNodes, setExpandedNodes] = useState({});
  const [activeGroup, setActiveGroup] = useState('All');
  const [query, setQuery] = useState('');
  const [editorMode, setEditorMode] = useState('preview');
  const requestedPath = searchParams.get('path') || '';

  const loadPrompts = useCallback(async (isActive = () => true) => {
    setLoading(true);
    setError(null);
    try {
      const response = await getPrompts();
      if (!isActive()) {
        return;
      }
      const items = response.prompts || [];
      setPrompts(items);
      setSelectedPath((currentSelected) => {
        const nextSelected = items.find((prompt) => prompt.path === requestedPath)?.path
          || items.find((prompt) => prompt.path === currentSelected)?.path
          || items[0]?.path
          || '';
        const nextPrompt = items.find((prompt) => prompt.path === nextSelected);
        setDraftContent(nextPrompt?.content || '');
        return nextSelected;
      });
    } catch (loadError) {
      if (isActive()) {
        setError(loadError);
      }
    } finally {
      if (isActive()) {
        setLoading(false);
      }
    }
  }, [getPrompts, requestedPath]);

  useEffect(() => {
    let active = true;
    loadPrompts(() => active);
    return () => {
      active = false;
    };
  }, [loadPrompts]);

  useEffect(() => {
    const currentPath = searchParams.get('path') || '';
    if (!selectedPath || currentPath === selectedPath) {
      return;
    }

    const nextParams = new URLSearchParams(searchParams);
    nextParams.set('path', selectedPath);
    setSearchParams(nextParams, { replace: true });
  }, [searchParams, selectedPath, setSearchParams]);

  const groupFilters = useMemo(
    () => ['All', ...Array.from(new Set(prompts.map((prompt) => prompt.primary_agent || prompt.category || 'unassigned'))).sort((left, right) => left.localeCompare(right))],
    [prompts],
  );
  const visiblePrompts = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return prompts.filter((prompt) => {
      const group = prompt.primary_agent || prompt.category || 'unassigned';
      const matchesGroup = activeGroup === 'All' || group === activeGroup;
      const matchesQuery = !normalizedQuery
        || `${prompt.filename} ${prompt.path} ${group}`.toLowerCase().includes(normalizedQuery);
      return matchesGroup && matchesQuery;
    });
  }, [activeGroup, prompts, query]);
  const promptTree = useMemo(
    () => buildPromptTree(visiblePrompts).map((group) => ({ ...group, ...sortTreeNode(group) })),
    [visiblePrompts]
  );
  const groupCounts = useMemo(
    () => visiblePrompts.reduce((counts, prompt) => {
      const group = prompt.primary_agent || prompt.category || 'unassigned';
      return { ...counts, [group]: (counts[group] || 0) + 1 };
    }, {}),
    [visiblePrompts],
  );
  const selectedPrompt = useMemo(
    () => prompts.find((prompt) => prompt.path === selectedPath) || null,
    [prompts, selectedPath]
  );
  const hasUnsavedChanges = selectedPrompt ? draftContent !== (selectedPrompt.content || '') : false;
  const renderedDraftContent = useMemo(() => renderMarkdownToHtml(draftContent), [draftContent]);

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

  const handleRefreshList = async () => {
    await loadPrompts();
  };

  return (
    <div className="prompts-page">
      <PaneSection as="aside" className={paneClassName} aria-label="Prompts" onItemSelect={() => setCollapsed(true)}>
        {collapsed ? (
          <PaneExpandRail label="Prompts" onExpand={() => setCollapsed(false)} />
        ) : (
        <>
        <PaneCollapseSeam label="Prompts" onCollapse={() => setCollapsed(true)} />
        <PaneListFilters
          filters={groupFilters}
          activeFilter={activeGroup}
          onFilterChange={setActiveGroup}
          actionLabel="Refresh"
          onAction={handleRefreshList}
        />
        <PaneListToolbar>
          <PaneListSearch value={query} placeholder="Search prompts" onChange={setQuery} />
          <button type="button" className="sess-sort" onClick={() => setExpandedNodes({})}>
            Reset
          </button>
        </PaneListToolbar>

        <PaneListScroll className="prompts-sidebar-body">
          {loading && <div className="prompts-empty-state">Loading prompts...</div>}
          {error && !loading && prompts.length === 0 && <div className="prompts-empty-state">Failed to load prompts.</div>}
          {!loading && prompts.length === 0 && (
            <div className="prompts-empty-state">No prompts found.</div>
          )}
          {!loading && visiblePrompts.length === 0 && prompts.length > 0 && (
            <div className="prompts-empty-state">No prompts match the current filters.</div>
          )}
          {!loading && promptTree.map((group) => (
            <PaneListGroup key={group.name} title={group.name} count={groupCounts[group.name] || 0}>
              <PromptTreeBranch
                label={group.name}
                branchKey={group.name}
                node={group}
                hideLabel
                selectedPath={selectedPath}
                expandedNodes={expandedNodes}
                onToggle={toggleBranch}
                onSelectPrompt={handleSelectPrompt}
              />
            </PaneListGroup>
          ))}
        </PaneListScroll>
        </>
        )}
      </PaneSection>

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
                <div className="prompts-editor-mode" role="tablist" aria-label="Prompt display mode">
                  <button
                    type="button"
                    className={`chat-pane-tab prompts-editor-mode__button ${editorMode === 'preview' ? 'is-active' : ''}`}
                    onClick={() => setEditorMode('preview')}
                  >
                    Preview
                  </button>
                  <button
                    type="button"
                    className={`chat-pane-tab prompts-editor-mode__button ${editorMode === 'edit' ? 'is-active' : ''}`}
                    onClick={() => setEditorMode('edit')}
                  >
                    Edit
                  </button>
                </div>
                <button className="node-action-btn" onClick={handleReload} disabled={!hasUnsavedChanges || saving}>
                  Revert
                </button>
                <button className="node-action-btn primary" onClick={handleSave} disabled={!hasUnsavedChanges || saving}>
                  {saving ? 'Saving...' : 'Save'}
                </button>
              </div>
            </div>

            {editorMode === 'preview' ? (
              <div
                className="prompts-markdown-preview message-content"
                dangerouslySetInnerHTML={{ __html: renderedDraftContent }}
              />
            ) : (
              <textarea
                className="prompts-editor-textarea"
                value={draftContent}
                onChange={(event) => setDraftContent(event.target.value)}
                spellCheck={false}
              />
            )}

            <div className="prompts-editor-footer">
              <span>{hasUnsavedChanges ? 'Unsaved changes' : 'Saved'}</span>
              <span>{statusMessage || error?.message || ''}</span>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
