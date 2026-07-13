import { useCallback, useEffect, useMemo, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useSearchParams } from 'react-router-dom';
import { selectRealtime } from '../store/realtimeSlice';
import { useConfig } from '../context/ConfigContext';
import {
  fetchSkills,
  saveSkill,
  createSkill,
  selectSkills,
  selectSkillsState,
} from '../store/uiDataSlice';
import { renderMarkdownToHtml } from '../lib/markdown';
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
  PaneExpandRail,
  PaneListFilters,
  PaneListGroup,
  PaneListScroll,
  PaneListSearch,
  PaneListToolbar,
} from '../components/layout/PaneList';
import { usePaneCollapse } from '../components/layout/usePaneCollapse';
import { PaneSection } from '../components/layout/PaneList';

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
  const [creating, setCreating] = useState(false);
  const [pendingCreatedSkill, setPendingCreatedSkill] = useState(null);
  const [error, setError] = useState(null);
  const [statusMessage, setStatusMessage] = useState('');
  const [activeGroup, setActiveGroup] = useState('All');
  const [query, setQuery] = useState('');
  const [editorMode, setEditorMode] = useState('preview');
  const [newAgent, setNewAgent] = useState('progenitor');
  const [newSkillName, setNewSkillName] = useState('new-skill');
  const requestedPath = searchParams.get('path') || '';
  const loading = skillsState.loading && !skillsState.loaded;

  // Request the catalogue only after the canonical stream is ready; the matching
  // `skills.snapshot` event is the sole source of skill data.
  const loadSkills = useCallback(async () => {
    setError(null);
    await dispatch(fetchSkills({ skillsUrl })).unwrap();
  }, [dispatch, skillsUrl]);

  useEffect(() => {
    if (!realtime.connected) {
      return;
    }
    // The acknowledgement failure is reflected in local view state.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadSkills().catch(setError);
  }, [loadSkills, realtime.connected]);
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
    // Canonical event application is the intentional source of this local UI transition.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSelectedPath(createdSkill.path);
    setDraftContent(createdSkill.content || '');
    setCreating(false);
    setSaving(false);
    setPendingCreatedSkill(null);
    setStatusMessage(`Created ${createdSkill.path}.`);
  }, [pendingCreatedSkill, skills, skillsState.snapshotRequestId]);

  // Seed (or re-seed) the selection from the store list whenever it changes via
  // fetch/snapshot, honoring a deep-linked `?path=` and keeping any still-valid
  // current selection. Runs on `skills`/`requestedPath` changes only (not on
  // user clicks), so selecting a row never gets clobbered.
  useEffect(() => {
    if (creating || pendingCreatedSkill || skills.length === 0) {
      return;
    }
    // Snapshot-driven selection intentionally synchronizes route-local view state.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSelectedPath((current) => {
      const next = skills.find((skill) => skill.path === requestedPath)?.path
        || skills.find((skill) => skill.path === current)?.path
        || skills[0]?.path
        || '';
      if (next !== current) {
        setDraftContent(skills.find((skill) => skill.path === next)?.content || '');
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
  const groupCounts = useMemo(
    () => visibleSkills.reduce((counts, skill) => {
      const group = skill.agent || 'progenitor';
      return { ...counts, [group]: (counts[group] || 0) + 1 };
    }, {}),
    [visibleSkills],
  );
  const selectedSkill = useMemo(
    () => skills.find((skill) => skill.path === selectedPath) || null,
    [selectedPath, skills],
  );
  const hasUnsavedChanges = selectedSkill ? draftContent !== (selectedSkill.content || '') : false;
  const renderedDraftContent = useMemo(() => renderMarkdownToHtml(draftContent), [draftContent]);

  useEffect(() => {
    if (
      !pendingSave?.acknowledged
      || skillsState.snapshotRequestId !== pendingSave.requestId
    ) {
      return;
    }
    const updatedSkill = skills.find((skill) => skill.path === pendingSave.path);
    if (!updatedSkill) {
      return;
    }
    // Canonical event application is the intentional source of this local UI transition.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDraftContent(updatedSkill.content || '');
    setSaving(false);
    setPendingSave(null);
    setStatusMessage(`Saved ${updatedSkill.path}.`);
  }, [pendingSave, skills, skillsState.snapshotRequestId]);
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
    setDraftContent(skill.content || '');
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
    setDraftContent(selectedSkill.content || '');
    setStatusMessage('Reverted unsaved changes.');
  };

  const handleSave = async () => {
    if (!selectedSkill || !hasUnsavedChanges || saving) {
      return;
    }
    const requestId = crypto.randomUUID();
    setSaving(true);
    setPendingSave({ requestId, path: selectedSkill.path, acknowledged: false });
    setError(null);
    setStatusMessage('');
    try {
      await dispatch(
        saveSkill({ skillsUrl, path: selectedSkill.path, content: draftContent, requestId }),
      ).unwrap();
      setPendingSave((current) => (
        current?.requestId === requestId ? { ...current, acknowledged: true } : current
      ));
    } catch (saveError) {
      setPendingSave(null);
      setSaving(false);
      setError(saveError);
    }
  };

  const handleCreateSkill = async () => {
    if (!canCreateSkill) {
      setError(new Error('Use lowercase letters, numbers, hyphens, or underscores for agent and skill names.'));
      return;
    }
    if (saving) {
      return;
    }
    const requestId = crypto.randomUUID();
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
      setPendingCreatedSkill(null);
      setSaving(false);
      setError(createError);
    }
  };

  const handleRefreshList = async () => {
    if (saving) {
      return;
    }
    setCreating(false);
    try {
      await loadSkills();
    } catch (loadError) {
      setError(loadError);
    }
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
          <button type="button" className="sess-sort" onClick={handleRefreshList}>
            Refresh
          </button>
        </PaneListToolbar>

        <PaneListScroll className="prompts-sidebar-body">
          {loading && <div className="prompts-empty-state">Loading skills...</div>}
          {(error || skillsState.error) && !loading && skills.length === 0 && <div className="prompts-empty-state">Failed to load skills.</div>}
          {!loading && skills.length === 0 && (
            <div className="prompts-empty-state">No skills found. Create one to share reusable behavior with agents.</div>
          )}
          {!loading && visibleSkills.length === 0 && skills.length > 0 && (
            <div className="prompts-empty-state">No skills match the current filters.</div>
          )}
          {!loading && skillTree.map((group) => (
            <PaneListGroup key={group.name} title={group.name} count={groupCounts[group.name] || 0}>
              <div className="prompts-tree-branch">
                <div className="prompts-tree-children">
                  {group.skills.map((skill) => (
                    <SkillRow
                      key={skill.path}
                      skill={skill}
                      selectedPath={selectedPath}
                      onSelectSkill={handleSelectSkill}
                    />
                  ))}
                </div>
              </div>
            </PaneListGroup>
          ))}
        </PaneListScroll>
        </>
        )}
      </PaneSection>

      <main className="skills-editor prompts-editor">
        {!selectedSkill && !creating && !loading && (
          <div className="prompts-empty-state">Select a skill to edit or create a new one.</div>
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
                      disabled={!canCreateSkill || saving}
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
                        disabled={!hasUnsavedChanges || saving}
                      />
                    </>
                  )}
                </DetailHeaderActions>
              )}
            />

            <div className="skills-detail__body">
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
              ) : (
                <p className="task-description skills-detail__description">
                  {selectedSkill.description || 'No description yet.'}
                </p>
              )}

              {editorMode === 'preview' && !creating ? (
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
            </div>

            <div className="prompts-editor-footer">
              <span>{creating ? (saving ? 'Saving...' : 'Draft skill') : (saving ? 'Saving...' : (hasUnsavedChanges ? 'Unsaved changes' : 'Saved'))}</span>
              <span>{statusMessage || error?.message || ''}</span>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
