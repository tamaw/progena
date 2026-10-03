import { usePaneCollapse } from '../components/layout/usePaneCollapse';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useSearchParams } from 'react-router-dom';
import { selectRealtime } from '../store/realtimeSlice';
import { useConfig } from '../context/ConfigContext';
import { renderMarkdownToHtml } from '../lib/markdown';
import { createUuid } from '../lib/uuid';
import {
  fetchPrompts,
  fetchPromptContent,
  savePrompt,
  selectPrompts,
  selectPromptsState,
  selectPromptContentEntry,
} from '../store/uiDataSlice';
import { SNAPSHOT_EVENT_TIMEOUT_MS, useSnapshotSave } from '../store/uiData/snapshotSave';
import {
  PaneCollapseSeam,
  PaneEmptyState,
  PaneExpandRail,
  PaneListFilters,
  PaneListSearch,
  PaneListToolbar,
  PaneListRows,
  PaneSection,
} from '../components/layout/PaneList';

function promptRefreshDisconnectedError() {
  return new Error('Prompt refresh could not be confirmed because the realtime stream disconnected. Reconnect and refresh again.');
}

function promptRefreshTimeoutError() {
  return new Error('Prompt refresh was acknowledged, but the updated snapshot did not arrive. Reconnect and refresh again.');
}

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

function flattenPromptNode(node, branchKey, depth, expandedNodes, hideLabel = false) {
  const isExpanded = hideLabel ? true : (expandedNodes[branchKey] ?? depth === 0);
  const rows = [];
  if (!hideLabel) {
    rows.push({ kind: 'branch', key: branchKey, label: node.name, branchKey, depth, isExpanded });
  }
  if (!isExpanded) {
    return rows;
  }
  node.children.forEach((child) => {
    const childKey = `${branchKey}/${child.name}`;
    rows.push(...flattenPromptNode(child, childKey, depth + 1, expandedNodes));
  });
  node.prompts.forEach((prompt) => {
    rows.push({ kind: 'prompt', key: prompt.path, prompt, depth: depth + 1 });
  });
  return rows;
}


export default function PromptsPage() {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const promptsUrl = getApiUrl('prompts');
  const realtime = useSelector(selectRealtime);
  const prompts = useSelector(selectPrompts);
  const promptsState = useSelector(selectPromptsState);
  const { collapsed, setCollapsed, paneClassName } = usePaneCollapse('prompts-sidebar sess-list');
  const [searchParams, setSearchParams] = useSearchParams();
  const [selectedPath, setSelectedPath] = useState('');
  const [draftContent, setDraftContent] = useState('');
  const [saving, setSaving] = useState(false);
  const [pendingSave, setPendingSave] = useState(null);
  const [pendingLoad, setPendingLoad] = useState(null);
  const [error, setError] = useState(null);
  const [statusMessage, setStatusMessage] = useState('');
  const [expandedNodes, setExpandedNodes] = useState({});
  const [activeGroup, setActiveGroup] = useState('All');
  const [query, setQuery] = useState('');
  const [editorMode, setEditorMode] = useState('preview');
  const promptContentEntry = useSelector((state) => selectPromptContentEntry(state, selectedPath));
  const initializedContentPathRef = useRef(null);
  const requestedPath = searchParams.get('path') || '';
  const loading = !promptsState.loaded && pendingLoad !== null;

  // Request the catalogue only after the canonical stream is ready; the matching
  // `prompts.snapshot` event is the sole source of prompt data.
  const loadPrompts = useCallback(async () => {
    if (!realtime.connected) {
      setError(new Error('Reconnect the realtime stream before refreshing prompts.'));
      return false;
    }
    const requestId = createUuid();
    setPendingLoad({ requestId, acknowledged: false });
    setError(null);
    try {
      await dispatch(fetchPrompts({ promptsUrl, requestId })).unwrap();
      setPendingLoad((current) => (
        current?.requestId === requestId ? { ...current, acknowledged: true } : current
      ));
      return true;
    } catch (loadError) {
      let failedCurrentLoad = false;
      setPendingLoad((current) => {
        failedCurrentLoad = current?.requestId === requestId;
        return failedCurrentLoad ? null : current;
      });
      if (failedCurrentLoad) {
        setError(loadError);
      }
      return false;
    }
  }, [dispatch, promptsUrl, realtime.connected]);

  useEffect(() => {
    if (!realtime.connected) {
      return;
    }
    let active = true;
    queueMicrotask(() => {
      if (active) {
        void loadPrompts();
      }
    });
    return () => {
      active = false;
    };
  }, [loadPrompts, realtime.connected]);

  useEffect(() => {
    if (!pendingLoad?.acknowledged || promptsState.snapshotRequestId !== pendingLoad.requestId) {
      return;
    }
    let active = true;
    queueMicrotask(() => {
      if (!active) {
        return;
      }
      setPendingLoad(null);
      setError(null);
    });
    return () => {
      active = false;
    };
  }, [pendingLoad, promptsState.snapshotRequestId]);

  useEffect(() => {
    if (!pendingLoad || realtime.connected) {
      return;
    }
    let active = true;
    queueMicrotask(() => {
      if (!active) {
        return;
      }
      setPendingLoad(null);
      setError(promptRefreshDisconnectedError());
    });
    return () => {
      active = false;
    };
  }, [pendingLoad, realtime.connected]);

  useEffect(() => {
    if (!pendingLoad?.acknowledged || !realtime.connected) {
      return undefined;
    }
    const timeoutId = setTimeout(() => {
      setPendingLoad(null);
      setError(promptRefreshTimeoutError());
    }, SNAPSHOT_EVENT_TIMEOUT_MS);
    return () => clearTimeout(timeoutId);
  }, [pendingLoad, realtime.connected]);
  // Seed (or re-seed) the selection from the store list whenever it changes via
  // fetch/snapshot, honoring a deep-linked `?path=` and keeping any still-valid
  // current selection. Runs on `prompts`/`requestedPath` changes only (not on
  // user clicks), so selecting a row never gets clobbered.
  useEffect(() => {
    if (prompts.length === 0) {
      return;
    }
    // Snapshot-driven selection intentionally synchronizes route-local view state.
    setSelectedPath((current) => {
      const next = prompts.find((prompt) => prompt.path === requestedPath)?.path
        || prompts.find((prompt) => prompt.path === current)?.path
        || prompts[0]?.path
        || '';
      if (next !== current) {
        setDraftContent('');
        initializedContentPathRef.current = null;
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

  // List items carry metadata only; fetch the full body for the selected path.
  useEffect(() => {
    if (!realtime.connected || !selectedPath || promptContentEntry?.path === selectedPath) {
      return;
    }
    dispatch(fetchPromptContent({ promptsUrl, path: selectedPath }))
      .unwrap()
      .catch((contentError) => setError(contentError));
  }, [dispatch, promptContentEntry, promptsUrl, realtime.connected, selectedPath]);

  // Seed the editor once per selected path when its content arrives, so a
  // later re-broadcast of the same event never clobbers in-progress edits.
  useEffect(() => {
    if (!promptContentEntry || promptContentEntry.path !== selectedPath) {
      return;
    }
    if (initializedContentPathRef.current === selectedPath) {
      return;
    }
    initializedContentPathRef.current = selectedPath;
    let active = true;
    queueMicrotask(() => {
      if (!active) {
        return;
      }
      setDraftContent(promptContentEntry.content || '');
    });
    return () => {
      active = false;
    };
  }, [promptContentEntry, selectedPath]);

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
  const promptGroups = useMemo(
    () => promptTree.map((group) => ({
      key: group.name,
      title: group.name,
      items: flattenPromptNode(group, group.name, 0, expandedNodes, true),
    })),
    [expandedNodes, promptTree],
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
  const hasUnsavedChanges = selectedPrompt && promptContentEntry ? draftContent !== (promptContentEntry.content || '') : false;
  const isPromptContentLoading = Boolean(selectedPrompt) && (!promptContentEntry || promptContentEntry.path !== selectedPath);
  const renderedDraftContent = useMemo(() => renderMarkdownToHtml(draftContent), [draftContent]);

  const handleSave = useSnapshotSave({
    items: prompts,
    snapshotRequestId: promptsState.snapshotRequestId,
    pendingSave,
    dispatch,
    thunk: savePrompt,
    path: selectedPrompt?.path,
    content: draftContent,
    savedContent: promptContentEntry?.content || '',
    saving,
    realtimeConnected: realtime.connected,
    setDraftContent,
    setSaving,
    setPendingSave,
    setStatusMessage,
    setError,
    resolveSavedContent: (path) => (promptContentEntry?.path === path ? promptContentEntry.content : undefined),
    buildArgs: ({ path, content, requestId }) => ({ promptsUrl, path, content, requestId }),
  });

  const toggleBranch = (branchKey) => {
    setExpandedNodes((current) => ({
      ...current,
      [branchKey]: !(current[branchKey] ?? true),
    }));
  };

  const handleSelectPrompt = (prompt) => {
    if (saving) {
      return;
    }
    setError(null);
    setSelectedPath(prompt.path);
    setDraftContent('');
    initializedContentPathRef.current = null;
    setStatusMessage('');
  };

  const handleReload = async () => {
    if (!selectedPrompt) {
      return;
    }
    setError(null);
    setDraftContent(promptContentEntry?.content || '');
    setStatusMessage('Reverted unsaved changes.');
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
          actionDisabled={!realtime.connected || saving || pendingLoad !== null}
        />
        <PaneListToolbar>
          <PaneListSearch value={query} placeholder="Search prompts" onChange={setQuery} />
          <button type="button" className="sess-sort" onClick={() => setExpandedNodes({})}>
            Reset
          </button>
        </PaneListToolbar>

        {loading && <PaneEmptyState>Loading prompts...</PaneEmptyState>}
        {(error || promptsState.error) && !loading && prompts.length === 0 && <PaneEmptyState>{error?.message || promptsState.error}</PaneEmptyState>}
        {!loading && !realtime.connected && prompts.length === 0 && !(error || promptsState.error) && (
          <PaneEmptyState>Reconnect the realtime stream to load prompts.</PaneEmptyState>
        )}
        {!loading && prompts.length === 0 && realtime.connected && !(error || promptsState.error) && (
          <PaneEmptyState>No prompts found.</PaneEmptyState>
        )}
        {!loading && visiblePrompts.length === 0 && prompts.length > 0 && (
          <PaneEmptyState>No prompts match the current filters.</PaneEmptyState>
        )}
        {!loading && promptGroups.length ? (
          <PaneListRows
            className="prompts-sidebar-body"
            groups={promptGroups}
            getItemKey={(row) => row.key}
            renderGroupHeader={(group) => (
              <div className="sess-group__head">
                {group.title} <span className="muted">· {groupCounts[group.title] || 0}</span>
              </div>
            )}
            renderItem={(row) => (
              row.kind === 'branch' ? (
                <button
                  className={`prompts-tree-toggle depth-${Math.min(row.depth, 3)} ${row.isExpanded ? 'expanded' : ''}`}
                  onClick={() => toggleBranch(row.branchKey)}
                  type="button"
                >
                  <span className="prompts-tree-caret">{row.isExpanded ? '▾' : '▸'}</span>
                  <span className="prompts-tree-label pane-row-caption">{row.label}</span>
                </button>
              ) : (
                <button
                  className={`prompts-tree-leaf depth-${Math.min(row.depth, 3)} ${selectedPath === row.prompt.path ? 'active' : ''}`}
                  onClick={() => handleSelectPrompt(row.prompt)}
                  type="button"
                  title={row.prompt.path}
                >
                  <span className="prompts-tree-file pane-row-title">{row.prompt.displayName}</span>
                  <span className="prompts-tree-file-path pane-row-caption">{row.prompt.path}</span>
                </button>
              )
            )}
          />
        ) : null}
        </>
        )}
      </PaneSection>

      <main className="prompts-editor">
        {!selectedPrompt && !loading && (
          <PaneEmptyState>Select a prompt to edit.</PaneEmptyState>
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
                <button className="node-action-btn primary" onClick={handleSave} disabled={!hasUnsavedChanges || saving || !realtime.connected}>
                  {saving ? 'Saving...' : 'Save'}
                </button>
              </div>
            </div>

            {isPromptContentLoading ? (
              <PaneEmptyState>Loading prompt…</PaneEmptyState>
            ) : editorMode === 'preview' ? (
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
                disabled={saving}
              />
            )}

            <div className="prompts-editor-footer">
              <span>{saving ? 'Saving...' : (hasUnsavedChanges ? 'Unsaved changes' : 'Saved')}</span>
              <span>{statusMessage || error?.message || (!realtime.connected ? 'Reconnect the realtime stream to refresh or save prompts.' : '')}</span>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
