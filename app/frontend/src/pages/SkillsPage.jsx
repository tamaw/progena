import { usePaneCollapse } from '../components/layout/usePaneCollapse';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useSearchParams } from 'react-router-dom';
import { selectRealtime } from '../store/realtimeSlice';
import { useConfig } from '../context/ConfigContext';
import { renderMarkdownToHtml, stripYamlFrontmatter } from '../lib/markdown';
import { createUuid } from '../lib/uuid';
import {
  fetchSkills,
  fetchSkillContent,
  saveSkill,
  createSkill,
  selectSkills,
  selectSkillsState,
  selectSkillContentEntry,
} from '../store/uiDataSlice';
import { SNAPSHOT_EVENT_TIMEOUT_MS, useSnapshotSave } from '../store/uiData/snapshotSave';
import DetailHeader, {
  DetailHeaderAction,
  DetailHeaderActions,
  DetailStatus,
  EditActionIcon,
  ReconnectActionIcon,
  SaveActionIcon,
} from '../components/layout/DetailHeader';
import {
  PaneCollapseSeam,
  PaneEmptyState,
  PaneExpandRail,
  PaneListFilters,
  PaneListSearch,
  PaneListToolbar,
  PaneListVirtualized,
  PaneSection,
} from '../components/layout/PaneList';

function skillRefreshDisconnectedError() {
  return new Error('Skill refresh could not be confirmed because the realtime stream disconnected. Reconnect and refresh again.');
}

function skillRefreshTimeoutError() {
  return new Error('Skill refresh was acknowledged, but the updated snapshot did not arrive. Reconnect and refresh again.');
}

function skillCreateDisconnectedError() {
  return new Error('Skill creation could not be confirmed because the realtime stream disconnected. Reconnect and refresh before trying again.');
}

function skillCreateTimeoutError() {
  return new Error('Skill creation was acknowledged, but the updated snapshot did not arrive. Refresh before trying again so you do not create duplicates.');
}
const SKILL_NAME_PATTERN = /^[a-z0-9][a-z0-9_-]*$/;

function titleFromSlug(slug) {
  return slug
    .split(/[-_]+/)
    .filter(Boolean)
    .map((part) => `${part.slice(0, 1).toUpperCase()}${part.slice(1)}`)
    .join(' ');
}

function buildSkillTemplate(agent, name) {
  const title = titleFromSlug(name) || name;
  return `---
name: ${title}
description: Describe when ${agent} should use this skill.
---

# ${title}

## When to use

Use this skill when the agent needs a repeatable workflow, domain guidance, or reusable operating procedure.

## Instructions

1. Describe the trigger conditions.
2. List the steps the agent should follow.
3. Include quality checks or examples that make the skill easy to reuse.
`;
}

function buildSkillTree(skills) {
  const groups = {};

  skills.forEach((skill) => {
    const agentKey = skill.agent || 'progenitor';
    if (!groups[agentKey]) {
      groups[agentKey] = [];
    }
    groups[agentKey].push(skill);
  });

  return Object.entries(groups)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([name, items]) => ({
      name,
      skills: [...items].sort((left, right) => {
        const leftLabel = left.title || left.name;
        const rightLabel = right.title || right.name;
        return leftLabel.localeCompare(rightLabel);
      }),
    }));
}

function normalizeSkillSegment(value) {
  return value.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '');
}

function SkillRow({ skill, selectedPath, onSelectSkill }) {
  return (
    <button
      key={skill.path}
      className={`prompts-tree-leaf skills-tree-leaf ${selectedPath === skill.path ? 'active' : ''}`}
      onClick={() => onSelectSkill(skill)}
      type="button"
      title={skill.path}
    >
      <span className="prompts-tree-file pane-row-title">{skill.title || skill.name}</span>
      <span className="prompts-tree-file-path pane-row-caption">{skill.description || skill.path}</span>
    </button>
  );
}

export default function SkillsPage() {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const skillsUrl = getApiUrl('skills');
  const realtime = useSelector(selectRealtime);
  const skills = useSelector(selectSkills);
  const skillsState = useSelector(selectSkillsState);
  const { collapsed, setCollapsed, paneClassName } = usePaneCollapse('skills-sidebar prompts-sidebar sess-list');
  const [searchParams, setSearchParams] = useSearchParams();
  const [selectedPath, setSelectedPath] = useState('');
  const [draftContent, setDraftContent] = useState('');
  const [saving, setSaving] = useState(false);
  const [pendingSave, setPendingSave] = useState(null);
  const [pendingLoad, setPendingLoad] = useState(null);
  const [creating, setCreating] = useState(false);
  const [pendingCreatedSkill, setPendingCreatedSkill] = useState(null);
  const [error, setError] = useState(null);
  const [statusMessage, setStatusMessage] = useState('');
  const [activeGroup, setActiveGroup] = useState('All');
  const [query, setQuery] = useState('');
  const [editorMode, setEditorMode] = useState('preview');
  const [newAgent, setNewAgent] = useState('progenitor');
  const [newSkillName, setNewSkillName] = useState('new-skill');
  const skillContentEntry = useSelector((state) => selectSkillContentEntry(state, selectedPath));
  const skillContentByPath = useSelector((state) => state.uiData.skills.contentByPath);
  const initializedContentPathRef = useRef(null);
  const requestedPath = searchParams.get('path') || '';
  const loading = !skillsState.loaded && pendingLoad !== null;

  // Request the catalogue only after the canonical stream is ready; the matching
  // `skills.snapshot` event is the sole source of skill data.
  const loadSkills = useCallback(async () => {
    if (!realtime.connected) {
      setError(new Error('Reconnect the realtime stream before refreshing skills.'));
      return false;
    }
    const requestId = createUuid();
    setPendingLoad({ requestId, acknowledged: false });
    setError(null);
    try {
      await dispatch(fetchSkills({ skillsUrl, requestId })).unwrap();
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
  }, [dispatch, realtime.connected, skillsUrl]);

  useEffect(() => {
    if (!realtime.connected) {
      return;
    }
    let active = true;
    queueMicrotask(() => {
      if (active) {
        void loadSkills();
      }
    });
    return () => {
      active = false;
    };
  }, [loadSkills, realtime.connected]);

  useEffect(() => {
    if (!pendingLoad?.acknowledged || skillsState.snapshotRequestId !== pendingLoad.requestId) {
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
  }, [pendingLoad, skillsState.snapshotRequestId]);

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
      setError(skillRefreshDisconnectedError());
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
      setError(skillRefreshTimeoutError());
    }, SNAPSHOT_EVENT_TIMEOUT_MS);
    return () => clearTimeout(timeoutId);
  }, [pendingLoad, realtime.connected]);

  // Creation is durable only once its own canonical snapshot is applied.
  useEffect(() => {
    if (
      !pendingCreatedSkill?.acknowledged
      || skillsState.snapshotRequestId !== pendingCreatedSkill.requestId
    ) {
      return;
    }
    const createdSkill = skills.find((skill) => (
      skill.agent === pendingCreatedSkill.agent && skill.name === pendingCreatedSkill.name
    ));
    if (!createdSkill) {
      return;
    }
    const createdContent = skillContentByPath[createdSkill.path]?.content;
    if (createdContent === undefined) {
      return; // wait for skill.content.updated to arrive before completing creation
    }
    // Canonical event application is the intentional source of this local UI transition.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSelectedPath(createdSkill.path);
    initializedContentPathRef.current = createdSkill.path;
    setDraftContent(createdContent);
    setCreating(false);
    setSaving(false);
    setPendingCreatedSkill(null);
    setStatusMessage(`Created ${createdSkill.path}.`);
  }, [pendingCreatedSkill, skillContentByPath, skills, skillsState.snapshotRequestId]);

  useEffect(() => {
    if (!pendingCreatedSkill || realtime.connected) {
      return;
    }
    let active = true;
    queueMicrotask(() => {
      if (!active) {
        return;
      }
      setSaving(false);
      setPendingCreatedSkill(null);
      setStatusMessage('');
      setError(skillCreateDisconnectedError());
    });
    return () => {
      active = false;
    };
  }, [pendingCreatedSkill, realtime.connected]);

  useEffect(() => {
    if (!pendingCreatedSkill?.acknowledged || !realtime.connected) {
      return undefined;
    }
    const timeoutId = setTimeout(() => {
      setSaving(false);
      setPendingCreatedSkill(null);
      setStatusMessage('');
      setError(skillCreateTimeoutError());
    }, SNAPSHOT_EVENT_TIMEOUT_MS);
    return () => clearTimeout(timeoutId);
  }, [pendingCreatedSkill, realtime.connected]);

  // Seed (or re-seed) the selection from the store list whenever it changes via
  // fetch/snapshot, honoring a deep-linked `?path=` and keeping any still-valid
  // current selection. Runs on `skills`/`requestedPath` changes only (not on
  // user clicks), so selecting a row never gets clobbered.
  useEffect(() => {
    if (creating || pendingCreatedSkill || skills.length === 0) {
      return;
    }
    // Snapshot-driven selection intentionally synchronizes route-local view state.
    setSelectedPath((current) => {
      const next = skills.find((skill) => skill.path === requestedPath)?.path
        || skills.find((skill) => skill.path === current)?.path
        || skills[0]?.path
        || '';
      if (next !== current) {
        setDraftContent('');
        initializedContentPathRef.current = null;
      }
      return next;
    });
  }, [skills, requestedPath, creating, pendingCreatedSkill]);

  useEffect(() => {
    const currentPath = searchParams.get('path') || '';
    if (creating || !selectedPath || currentPath === selectedPath) {
      return;
    }

    const nextParams = new URLSearchParams(searchParams);
    nextParams.set('path', selectedPath);
    setSearchParams(nextParams, { replace: true });
  }, [creating, searchParams, selectedPath, setSearchParams]);

  // List items carry metadata only; fetch the full body for the selected path.
  // Skipped while creating (there is no persisted path yet) or while a
  // just-created skill's content is still resolving (handled above).
  useEffect(() => {
    if (creating || !realtime.connected || !selectedPath || skillContentEntry?.path === selectedPath) {
      return;
    }
    dispatch(fetchSkillContent({ skillsUrl, path: selectedPath }))
      .unwrap()
      .catch((contentError) => setError(contentError));
  }, [creating, dispatch, realtime.connected, selectedPath, skillContentEntry, skillsUrl]);

  // Seed the editor once per selected path when its content arrives, so a
  // later re-broadcast of the same event never clobbers in-progress edits.
  useEffect(() => {
    if (creating || !skillContentEntry || skillContentEntry.path !== selectedPath) {
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
      setDraftContent(skillContentEntry.content || '');
    });
    return () => {
      active = false;
    };
  }, [creating, selectedPath, skillContentEntry]);

  const groupFilters = useMemo(
    () => ['All', ...Array.from(new Set(skills.map((skill) => skill.agent || 'progenitor'))).sort((left, right) => left.localeCompare(right))],
    [skills],
  );

  const visibleSkills = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return skills.filter((skill) => {
      const group = skill.agent || 'progenitor';
      const matchesGroup = activeGroup === 'All' || group === activeGroup;
      const searchable = `${skill.title} ${skill.name} ${skill.description} ${skill.path} ${group}`.toLowerCase();
      return matchesGroup && (!normalizedQuery || searchable.includes(normalizedQuery));
    });
  }, [activeGroup, query, skills]);

  const skillTree = useMemo(() => buildSkillTree(visibleSkills), [visibleSkills]);
  const selectedSkill = useMemo(
    () => skills.find((skill) => skill.path === selectedPath) || null,
    [selectedPath, skills],
  );
  const hasUnsavedChanges = selectedSkill && skillContentEntry ? draftContent !== (skillContentEntry.content || '') : false;
  const isSkillContentLoading = Boolean(selectedSkill) && !creating && (!skillContentEntry || skillContentEntry.path !== selectedPath);
  const renderedDraftContent = useMemo(
    () => renderMarkdownToHtml(stripYamlFrontmatter(draftContent)),
    [draftContent],
  );

  const handleSave = useSnapshotSave({
    items: skills,
    snapshotRequestId: skillsState.snapshotRequestId,
    pendingSave,
    dispatch,
    thunk: saveSkill,
    path: selectedSkill?.path,
    content: draftContent,
    savedContent: skillContentEntry?.content || '',
    saving,
    realtimeConnected: realtime.connected,
    setDraftContent,
    setSaving,
    setPendingSave,
    setStatusMessage,
    setError,
    resolveSavedContent: (path) => (skillContentEntry?.path === path ? skillContentEntry.content : undefined),
    buildArgs: ({ path, content, requestId }) => ({ skillsUrl, path, content, requestId }),
  });
  const normalizedNewAgent = normalizeSkillSegment(newAgent);
  const normalizedNewSkillName = normalizeSkillSegment(newSkillName);
  const canCreateSkill = SKILL_NAME_PATTERN.test(normalizedNewAgent) && SKILL_NAME_PATTERN.test(normalizedNewSkillName);

  const handleSelectSkill = (skill) => {
    if (saving) {
      return;
    }
    setPendingCreatedSkill(null);
    setCreating(false);
    setError(null);
    setSelectedPath(skill.path);
    setDraftContent('');
    initializedContentPathRef.current = null;
    setStatusMessage('');
  };

  const handleStartCreate = () => {
    if (saving) {
      return;
    }
    const defaultAgent = activeGroup !== 'All' ? activeGroup : 'progenitor';
    const agent = normalizeSkillSegment(defaultAgent) || 'progenitor';
    const skillName = 'new-skill';
    setCreating(true);
    setPendingCreatedSkill(null);
    setSelectedPath('');
    setNewAgent(agent);
    setNewSkillName(skillName);
    setDraftContent(buildSkillTemplate(agent, skillName));
    setEditorMode('edit');
    setStatusMessage('');
    setError(null);
    setSearchParams(new URLSearchParams(), { replace: true });
  };

  const handleReload = () => {
    if (!selectedSkill) {
      return;
    }
    setError(null);
    setDraftContent(skillContentEntry?.content || '');
    setStatusMessage('Reverted unsaved changes.');
  };


  const handleCreateSkill = async () => {
    if (!canCreateSkill) {
      setError(new Error('Use lowercase letters, numbers, hyphens, or underscores for agent and skill names.'));
      return;
    }
    if (saving) {
      return;
    }
    if (!realtime.connected) {
      setError(new Error('Reconnect the realtime stream before creating a skill.'));
      return;
    }
    const requestId = createUuid();
    setSaving(true);
    setPendingCreatedSkill({
      requestId,
      agent: normalizedNewAgent,
      name: normalizedNewSkillName,
      acknowledged: false,
    });
    setError(null);
    setStatusMessage('');
    try {
      await dispatch(
        createSkill({
          skillsUrl,
          agent: normalizedNewAgent,
          name: normalizedNewSkillName,
          content: draftContent,
          requestId,
        }),
      ).unwrap();
      setPendingCreatedSkill((current) => (
        current?.requestId === requestId ? { ...current, acknowledged: true } : current
      ));
    } catch (createError) {
      let failedCurrentCreate = false;
      setPendingCreatedSkill((current) => {
        failedCurrentCreate = current?.requestId === requestId;
        return failedCurrentCreate ? null : current;
      });
      setSaving(false);
      if (failedCurrentCreate) {
        setError(createError);
      }
    }
  };

  const handleRefreshList = async () => {
    await loadSkills();
  };

  return (
    <div className="skills-page prompts-page">
      <PaneSection as="aside" className={paneClassName} aria-label="Skills" onItemSelect={() => setCollapsed(true)}>
        {collapsed ? (
          <PaneExpandRail label="Skills" onExpand={() => setCollapsed(false)} />
        ) : (
        <>
        <PaneCollapseSeam label="Skills" onCollapse={() => setCollapsed(true)} />
        <PaneListFilters
          filters={groupFilters}
          activeFilter={activeGroup}
          onFilterChange={setActiveGroup}
          actionLabel="+ New"
          onAction={handleStartCreate}
        />
        <PaneListToolbar>
          <PaneListSearch value={query} placeholder="Search skills" onChange={setQuery} />
          <button type="button" className="sess-sort" onClick={handleRefreshList} disabled={!realtime.connected || saving || pendingLoad !== null}>
            Refresh
          </button>
        </PaneListToolbar>

        {loading && <PaneEmptyState>Loading skills...</PaneEmptyState>}
        {(error || skillsState.error) && !loading && skills.length === 0 && <PaneEmptyState>{error?.message || skillsState.error}</PaneEmptyState>}
        {!loading && !realtime.connected && skills.length === 0 && !(error || skillsState.error) && (
          <PaneEmptyState>Reconnect the realtime stream to load skills.</PaneEmptyState>
        )}
        {!loading && skills.length === 0 && realtime.connected && !(error || skillsState.error) && (
          <PaneEmptyState>No skills found. Create one to share reusable behavior with agents.</PaneEmptyState>
        )}
        {!loading && visibleSkills.length === 0 && skills.length > 0 && (
          <PaneEmptyState>No skills match the current filters.</PaneEmptyState>
        )}
        {!loading && skillTree.length ? (
          <PaneListVirtualized
            className="prompts-sidebar-body"
            groups={skillTree.map((group) => ({ ...group, items: group.skills }))}
            getItemKey={(skill) => skill.path}
            estimatedItemHeight={42}
            renderItem={(skill) => (
              <SkillRow
                skill={skill}
                selectedPath={selectedPath}
                onSelectSkill={handleSelectSkill}
              />
            )}
          />
        ) : null}
        </>
        )}
      </PaneSection>

      <main className="skills-editor prompts-editor">
        {!selectedSkill && !creating && !loading && (
          <PaneEmptyState>Select a skill to edit or create a new one.</PaneEmptyState>
        )}

        {(selectedSkill || creating) && (
          <>
            <DetailHeader
              title={creating ? 'New skill' : (selectedSkill.title || selectedSkill.name)}
              meta={[
                <DetailStatus key="status" tone={creating ? 'muted' : (saving || hasUnsavedChanges ? 'info' : 'ok')}>
                  {creating ? (saving ? 'Saving...' : 'Draft skill') : (saving ? 'Saving...' : (hasUnsavedChanges ? 'Unsaved changes' : 'Saved'))}
                </DetailStatus>,
                creating ? null : <span key="agent" className="taskid">{selectedSkill.agent}</span>,
                creating ? null : <span key="name" className="taskid">{selectedSkill.name}</span>,
                <span key="path" className="taskid">
                  {creating
                    ? (canCreateSkill ? `${normalizedNewAgent}/${normalizedNewSkillName}/SKILL.md` : 'Use lowercase slugs')
                    : selectedSkill.path}
                </span>,
              ]}
              actions={(
                <DetailHeaderActions>
                  {creating ? (
                    <DetailHeaderAction
                      icon={<SaveActionIcon />}
                      label="Create"
                      tooltip="Create skill"
                      ariaLabel="Create skill"
                      variant="primary"
                      onClick={handleCreateSkill}
                      disabled={!canCreateSkill || saving || !realtime.connected}
                    />
                  ) : (
                    <>
                      <DetailHeaderAction
                        icon={<EditActionIcon />}
                        label={editorMode === 'edit' ? 'Preview' : 'Edit'}
                        tooltip={editorMode === 'edit' ? 'Preview skill' : 'Edit skill'}
                        ariaLabel={editorMode === 'edit' ? 'Preview skill' : 'Edit skill'}
                        onClick={() => setEditorMode((mode) => (mode === 'edit' ? 'preview' : 'edit'))}
                      />
                      <DetailHeaderAction
                        icon={<ReconnectActionIcon />}
                        label="Revert"
                        tooltip="Revert unsaved changes"
                        ariaLabel="Revert unsaved changes"
                        onClick={handleReload}
                        disabled={!hasUnsavedChanges || saving}
                      />
                      <DetailHeaderAction
                        icon={<SaveActionIcon />}
                        label="Save"
                        tooltip="Save skill"
                        ariaLabel="Save skill"
                        variant="primary"
                        onClick={handleSave}
                        disabled={!hasUnsavedChanges || saving || !realtime.connected}
                      />
                    </>
                  )}
                </DetailHeaderActions>
              )}
            />

            <div className={`skills-detail__body${editorMode === 'preview' && !creating ? ' skills-detail__body--preview' : ''}`}>
              {creating ? (
                <div className="skills-create-form">
                  <label>
                    <span>Agent</span>
                    <input
                      value={newAgent}
                      onChange={(event) => setNewAgent(event.target.value)}
                      placeholder="progenitor"
                    />
                  </label>
                  <label>
                    <span>Skill</span>
                    <input
                      value={newSkillName}
                      onChange={(event) => setNewSkillName(event.target.value)}
                      placeholder="triage-workflow"
                    />
                  </label>
                </div>

              ) : isSkillContentLoading ? (
                <PaneEmptyState>Loading skill…</PaneEmptyState>
              ) : editorMode === 'preview' && !creating ? (
                <div
                  className="markdown-content"
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
            </div>

            <div className="prompts-editor-footer">
              <span>{creating ? (saving ? 'Saving...' : 'Draft skill') : (saving ? 'Saving...' : (hasUnsavedChanges ? 'Unsaved changes' : 'Saved'))}</span>
              <span>{statusMessage || error?.message || (!realtime.connected ? 'Reconnect the realtime stream to refresh, create, or save skills.' : '')}</span>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
