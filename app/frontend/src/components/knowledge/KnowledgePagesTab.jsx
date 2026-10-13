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
import { buildPageTree, dirOf, resolvePageLink } from '../../pages/knowledgePagesModel';

function findDir(tree, dirPath) {
  let node = tree;
  for (const part of dirPath.split('/').filter(Boolean)) {
    node = node.directories.find((dir) => dir.name === part);
    if (!node) return null;
  }
  return node;
}

// Path crumbs (base, folders, page). Each opens a list of its folder's files.
function Breadcrumbs({ baseName, tree, selectedPath, onSelect }) {
  const [openDir, setOpenDir] = useState(null);
  const ref = useRef(null);
  useEffect(() => {
    if (openDir === null) return undefined;
    const close = (event) => {
      if (event.type === 'keydown' ? event.key === 'Escape' : !ref.current?.contains(event.target)) setOpenDir(null);
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', close);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', close); };
  }, [openDir]);

  const dirs = dirOf(selectedPath).split('/').filter(Boolean);
  const crumbs = [{ label: baseName, dir: '' }];
  dirs.forEach((name, i) => crumbs.push({ label: name, dir: dirs.slice(0, i + 1).join('/') }));
  const fileName = selectedPath.split('/').at(-1);
  if (fileName !== 'index.md') {
    const node = findDir(tree, dirOf(selectedPath));
    const entry = node?.pages.find((p) => p.path === selectedPath);
    crumbs.push({ label: entry?.title || fileName, dir: dirOf(selectedPath), key: selectedPath });
  }
  const choose = (path) => { setOpenDir(null); onSelect(path); };

  return (
    <ol className="knowledge-breadcrumbs" ref={ref} aria-label="Page path">
      {crumbs.map((crumb) => {
        if (crumb.key) {
          return <li key={crumb.key}><span className="knowledge-crumb-current" aria-current="page">{crumb.label}</span></li>;
        }
        const key = `dir:${crumb.dir}`;
        const index = crumb.dir ? `${crumb.dir}/index.md` : 'index.md';
        const node = openDir === key ? findDir(tree, crumb.dir) : null;
        return (
          <li key={key}>
            <button type="button" aria-current={index === selectedPath ? 'page' : undefined} onClick={() => choose(index)}>
              {crumb.label}
            </button>
            <button
              type="button"
              className="knowledge-crumb-toggle"
              aria-label={`Files in ${crumb.label}`}
              aria-expanded={openDir === key}
              onClick={() => setOpenDir(openDir === key ? null : key)}
            >
              ▾
            </button>
            {node && (
              <ul className="knowledge-crumb-menu">
                {node.pages.filter((entry) => entry.name !== 'index.md').map((entry) => (
                  <li key={entry.path} className={entry.path === selectedPath ? 'is-selected' : ''}>
                    <button type="button" onClick={() => choose(entry.path)}>{entry.title || entry.name}</button>
                  </li>
                ))}
                {node.directories.map((dir) => (
                  <li key={dir.path}>
                    <button type="button" onClick={() => choose(`${dir.path}/index.md`)}>{dir.name}/</button>
                  </li>
                ))}
              </ul>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function frontMatterSources(frontMatter) {
  const sources = frontMatter && typeof frontMatter === 'object' ? frontMatter.sources : null;
  return (Array.isArray(sources) ? sources : sources ? [sources] : []).map(String);
}

function PageBody({ content, path, onNavigate }) {
  const html = useMemo(() => renderSafeMarkdown(content), [content]);
  const containerRef = useRef(null);
  useMermaidRender(containerRef, content);
  // Links between pages load in place instead of reloading the app.
  const handleClick = (event) => {
    const anchor = event.target.closest?.('a[href]');
    if (!anchor || event.metaKey || event.ctrlKey || event.shiftKey) return;
    const target = resolvePageLink(path, anchor.getAttribute('href'));
    if (target) {
      event.preventDefault();
      onNavigate(target);
    }
  };
  return <div ref={containerRef} className="markdown-content" onClick={handleClick} dangerouslySetInnerHTML={{ __html: html }} />;
}

export default function KnowledgePagesTab({ baseId, baseName, knowledgeUrl }) {
  const dispatch = useDispatch();
  const [params, setParams] = useSearchParams();
  const pages = useSelector((state) => selectKnowledgePages(state, baseId));
  const selectedPath = params.get('page') || 'index.md';
  const page = useSelector((state) => (selectedPath ? selectKnowledgePage(state, baseId, selectedPath) : null));
  const tree = useMemo(() => buildPageTree(pages), [pages]);
  const revisions = useSelector((state) => (selectedPath ? selectKnowledgeRevisions(state, baseId, selectedPath) : []));
  // The revision being previewed, tied to its page so navigating away drops it.
  const [viewingRevision, setViewingRevision] = useState(null);
  const viewingRevisionId = viewingRevision?.path === selectedPath ? viewingRevision.id : null;
  const setViewingRevisionId = (id) => setViewingRevision(id ? { path: selectedPath, id } : null);
  const revisionContent = useSelector((state) => (viewingRevisionId ? selectKnowledgeRevisionContent(state, baseId, selectedPath, viewingRevisionId) : null));
  const selectedPageMeta = useMemo(() => pages.find((p) => p.path === selectedPath) || null, [pages, selectedPath]);
  const lastFetchedAtRef = useRef(null);

  useEffect(() => { dispatch(fetchKnowledgePages({ knowledgeUrl, baseId })); }, [dispatch, knowledgeUrl, baseId]);

  useEffect(() => {
    if (selectedPath) {
      dispatch(fetchKnowledgePage({ knowledgeUrl, baseId, path: selectedPath }));
      dispatch(fetchKnowledgePageRevisions({ knowledgeUrl, baseId, path: selectedPath }));
      lastFetchedAtRef.current = null;
    }
  }, [dispatch, knowledgeUrl, baseId, selectedPath]);

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

  const selectPage = (path) => setParams(path === 'index.md' ? {} : { page: path });

  const isConcept = selectedPageMeta?.kind === 'concept';
  const sources = frontMatterSources(page?.front_matter);
  const displayContent = viewingRevisionId
    ? (revisionContent === null ? 'Loading…' : stripYamlFrontmatter(revisionContent))
    : (page?.body ?? page?.content ?? '');

  return (
    <div className="knowledge-pages-tab">
      <article>
        {!page ? <p>Loading…</p> : (
          <>
            <header className="knowledge-page-header">
              <Breadcrumbs baseName={baseName || baseId} tree={tree} selectedPath={selectedPath} onSelect={selectPage} />
              {isConcept && (
                <div className="knowledge-page-actions">
                  <button type="button" className="repo-btn repo-btn--ghost" onClick={handleRename}>Rename</button>
                  <button type="button" className="repo-btn repo-btn--ghost" onClick={handleDelete}>Delete</button>
                </div>
              )}
            </header>
            <PageBody content={displayContent} path={selectedPath} onNavigate={selectPage} />
            {sources.length > 0 && (
              <section className="knowledge-page-sources">
                <h3>Sources</h3>
                <ul>
                  {sources.map((source) => (
                    <li key={source}>
                      {/^https?:\/\//i.test(source)
                        ? <a href={source} target="_blank" rel="noopener noreferrer">{source}</a>
                        : <code>{source}</code>}
                    </li>
                  ))}
                </ul>
              </section>
            )}
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
