import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useSkillsApi } from '../hooks/api/useSkillsApi';
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
  const { getSkills, updateSkillContent, createSkill } = useSkillsApi();
  const { collapsed, setCollapsed, paneClassName } = usePaneCollapse('skills-sidebar prompts-sidebar sess-list');
  const [searchParams, setSearchParams] = useSearchParams();
  const [skills, setSkills] = useState([]);
  const [selectedPath, setSelectedPath] = useState('');
  const [draftContent, setDraftContent] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState(null);
  const [statusMessage, setStatusMessage] = useState('');
  const [activeGroup, setActiveGroup] = useState('All');
  const [query, setQuery] = useState('');
  const [editorMode, setEditorMode] = useState('preview');
  const [newAgent, setNewAgent] = useState('progenitor');
  const [newSkillName, setNewSkillName] = useState('new-skill');
  const requestedPath = searchParams.get('path') || '';

  const loadSkills = useCallback(async (isActive = () => true) => {
    setLoading(true);
    setError(null);
    try {
      const response = await getSkills();
      if (!isActive()) {
        return;
      }
      const items = response.skills || [];
      setSkills(items);
      setSelectedPath((currentSelected) => {
        const nextSelected = items.find((skill) => skill.path === requestedPath)?.path
          || items.find((skill) => skill.path === currentSelected)?.path
          || items[0]?.path
          || '';
        const nextSkill = items.find((skill) => skill.path === nextSelected);
        setDraftContent(nextSkill?.content || '');
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
  }, [getSkills, requestedPath]);

  useEffect(() => {
    let active = true;
    loadSkills(() => active);
    return () => {
      active = false;
    };
  }, [loadSkills]);

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
  const normalizedNewAgent = normalizeSkillSegment(newAgent);
  const normalizedNewSkillName = normalizeSkillSegment(newSkillName);
  const canCreateSkill = SKILL_NAME_PATTERN.test(normalizedNewAgent) && SKILL_NAME_PATTERN.test(normalizedNewSkillName);

  const handleSelectSkill = (skill) => {
    setCreating(false);
    setError(null);
    setSelectedPath(skill.path);
    setDraftContent(skill.content || '');
    setStatusMessage('');
  };

  const handleStartCreate = () => {
    const defaultAgent = activeGroup !== 'All' ? activeGroup : 'progenitor';
    const agent = normalizeSkillSegment(defaultAgent) || 'progenitor';
    const skillName = 'new-skill';
    setCreating(true);
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
    if (!selectedSkill || !hasUnsavedChanges) {
      return;
    }
    setSaving(true);
    setError(null);
    setStatusMessage('');
    try {
      const updatedSkill = await updateSkillContent(selectedSkill.path, draftContent);
      setSkills((current) => current.map((skill) => (
        skill.path === updatedSkill.path ? updatedSkill : skill
      )));
      setDraftContent(updatedSkill.content || '');
      setStatusMessage(`Saved ${updatedSkill.path}.`);
    } catch (saveError) {
      setError(saveError);
    } finally {
      setSaving(false);
    }
  };

  const handleCreateSkill = async () => {
    if (!canCreateSkill) {
      setError(new Error('Use lowercase letters, numbers, hyphens, or underscores for agent and skill names.'));
      return;
    }
    setSaving(true);
    setError(null);
    setStatusMessage('');
    try {
      const createdSkill = await createSkill({
        agent: normalizedNewAgent,
        name: normalizedNewSkillName,
        content: draftContent,
      });
      setSkills((current) => [...current.filter((skill) => skill.path !== createdSkill.path), createdSkill]);
      setCreating(false);
      setSelectedPath(createdSkill.path);
      setDraftContent(createdSkill.content || '');
      setStatusMessage(`Created ${createdSkill.path}.`);
      const nextParams = new URLSearchParams();
      nextParams.set('path', createdSkill.path);
      setSearchParams(nextParams, { replace: true });
    } catch (createError) {
      setError(createError);
    } finally {
      setSaving(false);
    }
  };

  const handleRefreshList = async () => {
    setCreating(false);
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
          <button type="button" className="sess-sort" onClick={handleRefreshList}>
            Refresh
          </button>
        </PaneListToolbar>

        <PaneListScroll className="prompts-sidebar-body">
          {loading && <div className="prompts-empty-state">Loading skills...</div>}
          {error && !loading && skills.length === 0 && <div className="prompts-empty-state">Failed to load skills.</div>}
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
            <div className="prompts-editor-header">
              <div className="skills-editor-title">
                <h2>{creating ? 'New skill' : (selectedSkill.title || selectedSkill.name)}</h2>
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
                    <span className="prompts-editor-path">
                      {canCreateSkill ? `${normalizedNewAgent}/${normalizedNewSkillName}/SKILL.md` : 'Use lowercase slugs'}
                    </span>
                  </div>
                ) : (
                  <>
                    <div className="prompts-editor-path">{selectedSkill.path}</div>
                    <div className="prompts-editor-badges">
                      <span className="prompts-editor-badge">{selectedSkill.agent}</span>
                      <span className="prompts-editor-badge">{selectedSkill.name}</span>
                    </div>
                  </>
                )}
              </div>
              <div className="prompts-editor-actions">
                <div className="prompts-editor-mode" role="tablist" aria-label="Skill display mode">
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
                {creating ? (
                  <button className="node-action-btn primary" onClick={handleCreateSkill} disabled={!canCreateSkill || saving}>
                    {saving ? 'Creating...' : 'Create'}
                  </button>
                ) : (
                  <>
                    <button className="node-action-btn" onClick={handleReload} disabled={!hasUnsavedChanges || saving}>
                      Revert
                    </button>
                    <button className="node-action-btn primary" onClick={handleSave} disabled={!hasUnsavedChanges || saving}>
                      {saving ? 'Saving...' : 'Save'}
                    </button>
                  </>
                )}
              </div>
            </div>

            {!creating && selectedSkill && (
              <div className="skills-meta-grid">
                <div>
                  <span>Agent</span>
                  <strong>{selectedSkill.agent}</strong>
                </div>
                <div>
                  <span>Reusable as</span>
                  <strong>{selectedSkill.name}</strong>
                </div>
                <div>
                  <span>Purpose</span>
                  <strong>{selectedSkill.description || 'No description yet'}</strong>
                </div>
              </div>
            )}

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
              <span>{creating ? 'Draft skill' : (hasUnsavedChanges ? 'Unsaved changes' : 'Saved')}</span>
              <span>{statusMessage || error?.message || ''}</span>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
