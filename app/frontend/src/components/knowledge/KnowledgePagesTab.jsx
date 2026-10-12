import {
  useEffect, useMemo, useRef, useState,
} from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useSearchParams } from 'react-router-dom';
import { useMermaidRender } from '../../hooks/useMermaidRender';
import { stripYamlFrontmatter } from '../../lib/markdown';
import { renderSafeMarkdown } from '../chat/messageRenderUtils';
import {
  deleteKnowledgePage,
  fetchKnowledgePage,
  fetchKnowledgePageRevision,
  fetchKnowledgePageRevisions,
  fetchKnowledgePages,
  renameKnowledgePage,
  restoreKnowledgePageRevision,
  selectKnowledgePage,
  selectKnowledgePages,
  selectKnowledgeRevisionContent,
  selectKnowledgeRevisions,
} from '../../store/uiDataSlice';
import { buildPageTree } from '../../pages/knowledgePagesModel';

function Tree({ node, selectedPath, onSelect }) {
  // A directory's own generated/reserved files come first, then subfolders, then
  // concept pages, so a folder's index.md never looks like a sibling of the root's.
  const page = (entry) => (
    <li key={entry.path} className={entry.path === selectedPath ? 'is-selected' : ''}>
      <button type="button" onClick={() => onSelect(entry.path)}>{entry.title || entry.name}</button>
    </li>
  );
  return <>
    {node.pages.filter((entry) => entry.kind !== 'concept').map(page)}
    {node.directories.map((dir) => (
      <li key={dir.path} className={`knowledge-tree-dir${selectedPath?.startsWith(`${dir.path}/`) ? ' is-open' : ''}`}>
        <strong>{dir.name}/</strong>
        <ul><Tree node={dir} selectedPath={selectedPath} onSelect={onSelect} /></ul>
      </li>
    ))}
    {node.pages.filter((entry) => entry.kind === 'concept').map(page)}
  </>;
}

function frontMatterFacts(frontMatter) {
  const fm = frontMatter && typeof frontMatter === 'object' ? frontMatter : {};
  const list = (value) => (Array.isArray(value) ? value.map(String) : value ? [String(value)] : []);
  return {
    type: fm.type ? String(fm.type) : null,
    tags: list(fm.tags),
    sources: list(fm.sources),
    generatedAt: fm.generated && typeof fm.generated === 'object' && fm.generated.at ? String(fm.generated.at) : null,
  };
}

function PageBody({ content }) {
  const html = useMemo(() => renderSafeMarkdown(content), [content]);
  const containerRef = useRef(null);
  useMermaidRender(containerRef, content);
  return <div ref={containerRef} className="markdown-content" dangerouslySetInnerHTML={{ __html: html }} />;
}

export default function KnowledgePagesTab({ baseId, knowledgeUrl }) {
  const dispatch = useDispatch();
  const [params, setParams] = useSearchParams();
  const pages = useSelector((state) => selectKnowledgePages(state, baseId));
  const selectedPath = params.get('page') || null;
  const page = useSelector((state) => (selectedPath ? selectKnowledgePage(state, baseId, selectedPath) : null));
  const tree = useMemo(() => buildPageTree(pages), [pages]);
  const revisions = useSelector((state) => (selectedPath ? selectKnowledgeRevisions(state, baseId, selectedPath) : []));
  const [viewingRevisionId, setViewingRevisionId] = useState(null);
  const revisionContent = useSelector((state) => (viewingRevisionId ? selectKnowledgeRevisionContent(state, baseId, selectedPath, viewingRevisionId) : null));
  const selectedPageMeta = useMemo(() => pages.find((p) => p.path === selectedPath) || null, [pages, selectedPath]);
  const lastFetchedAtRef = useRef(null);
  const lastSelectedPathRef = useRef(selectedPath);

  useEffect(() => { dispatch(fetchKnowledgePages({ knowledgeUrl, baseId })); }, [dispatch, knowledgeUrl, baseId]);

  useEffect(() => {
    if (selectedPath) {
      dispatch(fetchKnowledgePage({ knowledgeUrl, baseId, path: selectedPath }));
      dispatch(fetchKnowledgePageRevisions({ knowledgeUrl, baseId, path: selectedPath }));
      lastFetchedAtRef.current = null;
    }
  }, [dispatch, knowledgeUrl, baseId, selectedPath]);

  if (selectedPath !== lastSelectedPathRef.current) {
    lastSelectedPathRef.current = selectedPath;
    setViewingRevisionId(null);
  }

  // Refetch the selected page and its revisions when its updated_at in the snapshot changes.
  useEffect(() => {
    if (selectedPath && selectedPageMeta?.updated_at && selectedPageMeta.updated_at !== lastFetchedAtRef.current) {
      lastFetchedAtRef.current = selectedPageMeta.updated_at;
      dispatch(fetchKnowledgePage({ knowledgeUrl, baseId, path: selectedPath }));
      dispatch(fetchKnowledgePageRevisions({ knowledgeUrl, baseId, path: selectedPath }));
    }
  }, [dispatch, knowledgeUrl, baseId, selectedPath, selectedPageMeta?.updated_at]);

  useEffect(() => {
    if (viewingRevisionId) {
      dispatch(fetchKnowledgePageRevision({ knowledgeUrl, baseId, path: selectedPath, revisionId: viewingRevisionId }));
    }
  }, [dispatch, knowledgeUrl, baseId, selectedPath, viewingRevisionId]);

  const handleDelete = async () => {
    if (!selectedPath) return;
    if (!window.confirm(`Delete ${selectedPath}?`)) return;
    try {
      await dispatch(deleteKnowledgePage({ knowledgeUrl, baseId, path: selectedPath })).unwrap();
      setParams({});
    } catch (error) {
      window.alert(error?.message || 'Delete failed.');
    }
  };

  const handleRename = async () => {
    if (!selectedPath) return;
    const newPath = window.prompt('New path:', selectedPath)?.trim();
    if (!newPath || newPath === selectedPath) return;
    try {
      await dispatch(renameKnowledgePage({ knowledgeUrl, baseId, path: selectedPath, newPath })).unwrap();
      setParams({ page: newPath });
    } catch (error) {
      window.alert(error?.message || 'Rename failed.');
    }
  };

  const handleRestore = (revisionId) => {
    if (!window.confirm('Restoring saves this revision as the live page.')) return;
    dispatch(restoreKnowledgePageRevision({ knowledgeUrl, baseId, path: selectedPath, revisionId }));
    setViewingRevisionId(null);
  };

  const isConcept = selectedPageMeta?.kind === 'concept';
  const facts = frontMatterFacts(page?.front_matter);
  const hasFacts = Boolean(facts.type || facts.tags.length || facts.sources.length || facts.generatedAt);
  const displayContent = viewingRevisionId
    ? (revisionContent === null ? 'Loading…' : stripYamlFrontmatter(revisionContent))
    : (page?.body ?? page?.content ?? '');

  return (
    <div className="knowledge-pages-tab">
      <aside>
        <ul>
          <Tree node={tree} selectedPath={selectedPath} onSelect={(path) => setParams({ page: path })} />
        </ul>
      </aside>
      <article>
        {!selectedPath ? <p>Select a page.</p> : !page ? <p>Loading…</p> : (
          <>
            <header className="knowledge-page-header">
              <h2>{selectedPath}</h2>
              {isConcept && (
                <div className="knowledge-page-actions">
                  <button type="button" className="repo-btn repo-btn--ghost" onClick={handleRename}>Rename</button>
                  <button type="button" className="repo-btn repo-btn--ghost" onClick={handleDelete}>Delete</button>
                </div>
              )}
            </header>
            {hasFacts && (
              <dl className="knowledge-page-facts">
                {facts.type ? <><dt>Type</dt><dd>{facts.type}</dd></> : null}
                {facts.tags?.length ? <><dt>Tags</dt><dd>{facts.tags.join(', ')}</dd></> : null}
                {facts.sources?.length ? <><dt>Sources</dt><dd>{facts.sources.join(', ')}</dd></> : null}
                {facts.generatedAt ? <><dt>Generated</dt><dd>{facts.generatedAt}</dd></> : null}
              </dl>
            )}
            <PageBody content={displayContent} />
            {revisions.length > 0 && (
              <section className="knowledge-page-revisions">
                <h3>Revisions</h3>
                <ul>
                  {revisions.map((rev) => (
                    <li key={rev.revision_id}>
                      <button
                        type="button"
                        className={viewingRevisionId === rev.revision_id ? 'repo-btn' : 'repo-btn repo-btn--ghost'}
                        onClick={() => setViewingRevisionId(viewingRevisionId === rev.revision_id ? null : rev.revision_id)}
                      >
                        {rev.captured_at ? new Date(rev.captured_at).toLocaleString() : rev.revision_id.slice(0, 8)}
                      </button>
                      {viewingRevisionId === rev.revision_id && (
                        <button type="button" className="repo-btn repo-btn--ghost" onClick={() => handleRestore(rev.revision_id)}>Restore</button>
                      )}
                    </li>
                  ))}
                </ul>
                {viewingRevisionId && revisionContent !== null && (
                  <p className="task-meta-sub">Viewing revision {viewingRevisionId.slice(0, 8)} — click the revision again to return to the live page.</p>
                )}
              </section>
            )}
          </>
        )}
      </article>
    </div>
  );
}
