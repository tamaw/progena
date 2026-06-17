import { memo, useMemo, useState } from 'react';

import {
  PaneCollapseSeam,
  PaneExpandRail,
  PaneListFilters,
  PaneListToolbar,
  PaneListSearch,
  PaneListScroll,
  PaneListGroup,
  PaneListFooter,
} from '../layout/PaneList';
import { usePaneCollapse } from '../layout/usePaneCollapse';

const WORKSPACE_FILTERS = ['All', 'Flow', 'Research'];
const INITIAL_WORKSPACES = [
  {
    id: 'workspace-release',
    name: 'Release prep board',
    type: 'Squad',
    updated: '2m',
    summary: 'Ship readiness, blockers, and rollout notes.',
    items: 8,
  },
  {
    id: 'workspace-customer-journey',
    name: 'Customer journey map',
    type: 'Flow',
    updated: '18m',
    summary: 'UI states, edge cases, and next-step branches.',
    items: 6,
  },
  {
    id: 'workspace-agent-research',
    name: 'Agent research lab',
    type: 'Research',
    updated: '1h',
    summary: 'Experiments, findings, and prompt comparisons.',
    items: 11,
  },
  {
    id: 'workspace-ops-routines',
    name: 'Ops routines',
    type: 'Automation',
    updated: 'Today',
    summary: 'Scheduled jobs, alerts, and maintenance flows.',
    items: 5,
  },
];

function WorkspaceSidebar() {
  const { collapsed, setCollapsed, paneClassName } = usePaneCollapse('workspace-sidebar sess-list');
  const [workspaces, setWorkspaces] = useState(INITIAL_WORKSPACES);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState(INITIAL_WORKSPACES[0]?.id || null);
  const [activeType, setActiveType] = useState('All');
  const [query, setQuery] = useState('');
  const [editingWorkspaceId, setEditingWorkspaceId] = useState(null);
  const [draftName, setDraftName] = useState('');

  const filteredWorkspaces = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return workspaces.filter((workspace) => {
      const matchesType = activeType === 'All' || workspace.type === activeType;
      const matchesQuery = !normalizedQuery
        || `${workspace.name} ${workspace.summary}`.toLowerCase().includes(normalizedQuery);
      return matchesType && matchesQuery;
    });
  }, [activeType, query, workspaces]);

  const selectedWorkspace = workspaces.find((workspace) => workspace.id === selectedWorkspaceId) || workspaces[0] || null;

  const startEditing = (workspace) => {
    if (!workspace) {
      return;
    }
    setSelectedWorkspaceId(workspace.id);
    setEditingWorkspaceId(workspace.id);
    setDraftName(workspace.name);
  };

  const commitName = () => {
    if (!editingWorkspaceId) {
      return;
    }

    const trimmedName = draftName.trim();
    if (trimmedName) {
      setWorkspaces((current) => current.map((workspace) => (
        workspace.id === editingWorkspaceId
          ? { ...workspace, name: trimmedName }
          : workspace
      )));
    }

    setEditingWorkspaceId(null);
    setDraftName('');
  };

  const createWorkspace = () => {
    const id = `workspace-${Date.now()}`;
    const nextWorkspace = {
      id,
      name: 'Untitled workflow',
      type: activeType === 'All' ? 'Squad' : activeType,
      updated: 'now',
      summary: 'New canvas ready for notes, widgets, and layout experiments.',
      items: 0,
    };

    setWorkspaces((current) => [nextWorkspace, ...current]);
    setSelectedWorkspaceId(id);
    setEditingWorkspaceId(id);
    setDraftName(nextWorkspace.name);
  };

  return (
    <aside className={paneClassName} aria-label="Workflows">
      {collapsed ? (
        <PaneExpandRail label="Workflows" onExpand={() => setCollapsed(false)} />
      ) : (
        <>
          <PaneCollapseSeam label="Workflows" onCollapse={() => setCollapsed(true)} />
          <PaneListFilters
            filters={WORKSPACE_FILTERS}
            activeFilter={activeType}
            onFilterChange={setActiveType}
            actionLabel="+ New"
            onAction={createWorkspace}
          />

          <PaneListToolbar>
            <PaneListSearch value={query} placeholder="Search workflows" onChange={setQuery} />
            <button
              type="button"
              className="sess-sort"
              onClick={() => startEditing(selectedWorkspace)}
              disabled={!selectedWorkspace}
            >
              Rename
            </button>
          </PaneListToolbar>

          <PaneListScroll className="workspace-sidebar__scroll">
            <PaneListGroup title="Workflow selector" count={filteredWorkspaces.length}>
              {filteredWorkspaces.length ? filteredWorkspaces.map((workspace) => {
                const isSelected = workspace.id === selectedWorkspaceId;
                const isEditing = workspace.id === editingWorkspaceId;

                return (
                  <div
                    key={workspace.id}
                    className={`workspace-selector-row ${isSelected ? 'is-selected' : ''}`}
                  >
                    {isEditing ? (
                      <div className="workspace-selector-row__edit">
                        <input
                          value={draftName}
                          className="workspace-selector-row__input"
                          onChange={(event) => setDraftName(event.target.value)}
                          onBlur={commitName}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter') {
                              commitName();
                            } else if (event.key === 'Escape') {
                              setEditingWorkspaceId(null);
                              setDraftName('');
                            }
                          }}
                          autoFocus
                        />
                        <div className="workspace-selector-row__meta pane-row-caption">
                          <span className="pane-row-caption">{workspace.type}</span>
                          <span className="pane-row-caption">{workspace.updated}</span>
                        </div>
                      </div>
                    ) : (
                      <button
                        type="button"
                        className="workspace-selector-row__button"
                        onClick={() => setSelectedWorkspaceId(workspace.id)}
                        onDoubleClick={() => startEditing(workspace)}
                      >
                        <span className="workspace-selector-row__top">
                          <span className="workspace-selector-row__type pane-row-caption">{workspace.type}</span>
                          <span className="workspace-selector-row__updated pane-row-caption">{workspace.updated}</span>
                        </span>
                        <span className="workspace-selector-row__title pane-row-title">{workspace.name}</span>
                        <span className="workspace-selector-row__bottom">
                          <span className="workspace-selector-row__summary pane-row-caption">{workspace.summary}</span>
                          <span className="workspace-selector-row__foot pane-row-caption">
                            <span className="num">{workspace.items}</span> items
                          </span>
                        </span>
                      </button>
                    )}
                  </div>
                );
              }) : (
                <div className="empty-state small">No workflows match the current search.</div>
              )}
            </PaneListGroup>
          </PaneListScroll>

          <PaneListFooter>
            <span>{selectedWorkspace ? selectedWorkspace.type : 'No workflow'}</span>
            <span>{selectedWorkspace ? selectedWorkspace.name : 'Create one'}</span>
          </PaneListFooter>
        </>
      )}
    </aside>
  );
}

export default memo(WorkspaceSidebar);
