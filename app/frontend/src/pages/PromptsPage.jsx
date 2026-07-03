import { useCallback, useEffect, useMemo, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useSearchParams } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import {
  fetchPrompts,
  savePrompt,
  selectPrompts,
  selectPromptsState,
} from '../store/uiDataSlice';
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
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const promptsUrl = getApiUrl('prompts');
  const prompts = useSelector(selectPrompts);
  const promptsState = useSelector(selectPromptsState);
  const { collapsed, setCollapsed, paneClassName } = usePaneCollapse('prompts-sidebar sess-list');
  const [searchParams, setSearchParams] = useSearchParams();
  const [selectedPath, setSelectedPath] = useState('');
  const [draftContent, setDraftContent] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [statusMessage, setStatusMessage] = useState('');
  const [expandedNodes, setExpandedNodes] = useState({});
  const [activeGroup, setActiveGroup] = useState('All');
  const [query, setQuery] = useState('');
  const [editorMode, setEditorMode] = useState('preview');
  const requestedPath = searchParams.get('path') || '';
  const loading = promptsState.loading && !promptsState.loaded;

  // Load the catalogue into the store; the list arrives via the fetch response
  // and stays live through `prompts.snapshot` events on /api/ui/stream. The
  // `isActive` guard avoids a setError after the page unmounts mid-request.
  const loadPrompts = useCallback(
    (isActive = () => true) => dispatch(fetchPrompts({ promptsUrl }))
      .unwrap()
      .catch((loadError) => {
        if (isActive()) {
          setError(loadError);
        }
      }),
    [dispatch, promptsUrl],
  );

  useEffect(() => {
    let active = true;
    loadPrompts(() => active);
    return () => {
      active = false;
    };
  }, [loadPrompts]);

  // Seed (or re-seed) the selection from the store list whenever it changes via
  // fetch/snapshot, honoring a deep-linked `?path=` and keeping any still-valid
  // current selection. Runs on `prompts`/`requestedPath` changes only (not on
  // user clicks), so selecting a row never gets clobbered.
  useEffect(() => {
    if (prompts.length === 0) {
      return;
    }
    setSelectedPath((current) => {
      const next = prompts.find((prompt) => prompt.path === requestedPath)?.path
        || prompts.find((prompt) => prompt.path === current)?.path
        || prompts[0]?.path
        || '';
      if (next !== current) {
        setDraftContent(prompts.find((prompt) => prompt.path === next)?.content || '');
      }
      return next;
    });
  }, [prompts, requestedPath]);

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
      const updatedPrompt = await dispatch(
        savePrompt({ promptsUrl, path: selectedPrompt.path, content: draftContent }),
      ).unwrap();
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
