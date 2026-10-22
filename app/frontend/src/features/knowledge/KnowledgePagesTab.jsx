import {
  useCallback, useEffect, useMemo, useRef, useState,
} from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useSearchParams } from 'react-router-dom';
import { stripYamlFrontmatter } from '../../lib/markdown';
import MarkdownContent from '../../components/MarkdownContent';
import InlineFailure from '../../components/InlineFailure';
import { renderSafeMarkdown } from '../../lib/safeMarkdown';
import { useMenuDismiss } from '../../hooks/useMenuDismiss';
import { fetchKnowledgePage, fetchKnowledgePageRevision, fetchKnowledgePageRevisions, fetchKnowledgePages, selectKnowledgePage, selectKnowledgePageRequest, selectKnowledgePages, selectKnowledgeRevision, selectKnowledgePageRevisionRequest, selectKnowledgePageRevisionsRequest, selectKnowledgeRevisions } from './state';
import {
  buildPageTree, dirOf, formatRevisionTime, resolvePageLink,
} from './knowledgePagesModel';

function findDir(tree, dirPath) {
  let node = tree;
  for (const part of dirPath.split('/').filter(Boolean)) {
    node = node.directories.find((dir) => dir.name === part);
    if (!node) return null;
  }
  return node;
}

// Kind marker for a folder or file, shown in the breadcrumb and its menus.
function PathIcon({ kind }) {
  const paths = {
    folder: 'M2.5 4.5a1 1 0 0 1 1-1h3l1.5 1.5h4.5a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1z',
    log: 'M4 3.5h8v9H4zM6 6h4M6 8h4M6 10h2.5',
    page: 'M4 2.5h5.5L12 5v8.5H4zM9.5 2.5V5H12',
  };
  return (
    <svg className="knowledge-path-icon" viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <path d={paths[kind]} fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

// Path crumbs (base, folders, page). Each opens a list of its folder's files.
function Breadcrumbs({ baseName, tree, selectedPath, onSelect }) {
  const [openDir, setOpenDir] = useState(null);
  const ref = useRef(null);
  useMenuDismiss(openDir !== null, ref, () => setOpenDir(null));

  const dirs = dirOf(selectedPath).split('/').filter(Boolean);
  // The root crumb is the base's top folder: icon only, its name is in the header.
  const crumbs = [{ label: '', name: baseName, dir: '' }];
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
          return (
            <li key={crumb.key}>
              <span className="knowledge-crumb-current" aria-current="page">
                <PathIcon kind={fileName === 'log.md' ? 'log' : 'page'} />
                {crumb.label}
              </span>
            </li>
          );
        }
        const key = `dir:${crumb.dir}`;
        const index = crumb.dir ? `${crumb.dir}/index.md` : 'index.md';
        const node = openDir === key ? findDir(tree, crumb.dir) : null;
        return (
          <li key={key}>
            <button
              type="button"
              aria-label={crumb.dir ? undefined : `${baseName} root`}
              aria-current={index === selectedPath ? 'page' : undefined}
              onClick={() => choose(index)}
            >
              <PathIcon kind="folder" />
              {crumb.label}
            </button>
            <button
              type="button"
              className="knowledge-crumb-toggle"
              aria-label={`Files in ${crumb.name || crumb.label}`}
              aria-expanded={openDir === key}
              onClick={() => setOpenDir(openDir === key ? null : key)}
            >
              ▾
            </button>
            {node && (
              <ul className="knowledge-crumb-menu">
                {node.pages.filter((entry) => entry.name !== 'index.md').map((entry) => (
                  <li key={entry.path} className={entry.path === selectedPath ? 'is-selected' : ''}>
                    <button type="button" onClick={() => choose(entry.path)}>
                      <PathIcon kind={entry.name === 'log.md' ? 'log' : 'page'} />
                      <span>{entry.title || entry.name}</span>
                    </button>
                  </li>
                ))}
                {node.directories.map((dir) => (
                  <li key={dir.path}>
                    <button type="button" onClick={() => choose(`${dir.path}/index.md`)}>
                      <PathIcon kind="folder" />
                      <span>{dir.name}/</span>
                    </button>
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

// Reader URL (relative to the current base) for a page path.
function pageHref(target) {
  return target === 'index.md' ? '?' : `?${new URLSearchParams({ page: target })}`;
}

// Internal page links get a real reader URL so new-tab/middle-click open the
// right page; the resolved path is kept on the anchor for in-place navigation.
// Done on the HTML string (not the live DOM) so React re-renders keep it.
function withReaderLinks(html, path) {
  const template = document.createElement('template');
  template.innerHTML = html;
  template.content.querySelectorAll('a[href]').forEach((anchor) => {
    const target = resolvePageLink(path, anchor.getAttribute('href'));
    if (target) {
      anchor.dataset.page = target;
      anchor.setAttribute('href', pageHref(target));
    }
  });
  return template.innerHTML;
}

function PageBody({ content, path, onNavigate }) {
  const transformHtml = useCallback((html) => withReaderLinks(html, path), [path]);
  // Links between pages load in place instead of reloading the app.
  const handleClick = (event) => {
    const anchor = event.target.closest?.('a[href]');
    if (!anchor || event.metaKey || event.ctrlKey || event.shiftKey) return;
    const href = anchor.getAttribute('href');
    if (anchor.dataset.page) {
      event.preventDefault();
      onNavigate(anchor.dataset.page);
    } else if (/^https?:\/\//i.test(href)) {
      // Web links leave the app in a new tab, like the Sources list.
      event.preventDefault();
      window.open(href, '_blank', 'noopener,noreferrer');
    }
  };
  return (
    <MarkdownContent
      content={content}
      onClick={handleClick}
      renderMarkdown={renderSafeMarkdown}
      renderMermaid
      transformHtml={transformHtml}
    />
  );
}

export default function KnowledgePagesTab({ baseId, baseName, knowledgeUrl }) {
  const dispatch = useDispatch();
  const [params, setParams] = useSearchParams();
  const pages = useSelector((state) => selectKnowledgePages(state, baseId));
  const selectedPath = params.get('page') || 'index.md';
  const page = useSelector((state) => (selectedPath ? selectKnowledgePage(state, baseId, selectedPath) : null));
  const pageRequest = useSelector((state) => selectKnowledgePageRequest(state, baseId, selectedPath));
  const tree = useMemo(() => buildPageTree(pages), [pages]);
  const revisions = useSelector((state) => (selectedPath ? selectKnowledgeRevisions(state, baseId, selectedPath) : []));
  const revisionsRequest = useSelector((state) => selectKnowledgePageRevisionsRequest(state, baseId, selectedPath));
  // The revision being previewed lives in ?revision= so the detail header can show it.
  const viewingRevisionId = params.get('revision');
  const revision = useSelector((state) => (viewingRevisionId ? selectKnowledgeRevision(state, baseId, selectedPath, viewingRevisionId) : null));
  const revisionRequest = useSelector((state) => selectKnowledgePageRevisionRequest(
    state, baseId, selectedPath, viewingRevisionId,
  ));
  const selectedPageMeta = useMemo(() => pages.find((p) => p.path === selectedPath) || null, [pages, selectedPath]);
  const lastFetchedAtRef = useRef(null);

  useEffect(() => { dispatch(fetchKnowledgePages({ knowledgeUrl, baseId })); }, [dispatch, knowledgeUrl, baseId]);

  const loadPage = useCallback(
    (path = selectedPath) => dispatch(fetchKnowledgePage({ knowledgeUrl, baseId, path })),
    [baseId, dispatch, knowledgeUrl, selectedPath],
  );
  const loadRevisions = useCallback(
    (path = selectedPath) => dispatch(fetchKnowledgePageRevisions({ knowledgeUrl, baseId, path })),
    [baseId, dispatch, knowledgeUrl, selectedPath],
  );
  const loadRevision = useCallback(
    (revisionId = viewingRevisionId) => dispatch(fetchKnowledgePageRevision({
      knowledgeUrl, baseId, path: selectedPath, revisionId,
    })),
    [baseId, dispatch, knowledgeUrl, selectedPath, viewingRevisionId],
  );

  useEffect(() => {
    if (selectedPath) {
      loadPage(selectedPath);
      loadRevisions(selectedPath);
      lastFetchedAtRef.current = null;
    }
  }, [loadPage, loadRevisions, selectedPath]);

  // Refetch the selected page and its revisions when its updated_at in the snapshot changes.
  useEffect(() => {
    if (selectedPath && selectedPageMeta?.updated_at && selectedPageMeta.updated_at !== lastFetchedAtRef.current) {
      lastFetchedAtRef.current = selectedPageMeta.updated_at;
      loadPage(selectedPath);
      loadRevisions(selectedPath);
    }
  }, [loadPage, loadRevisions, selectedPath, selectedPageMeta?.updated_at]);

  // A generated index.md isn't a stored page, so it has no updated_at of its own:
  // refetch it whenever the base's pages snapshot changes (any page added,
  // edited, deprecated or removed).
  const pagesSignature = useMemo(() => pages.map((p) => `${p.path}@${p.updated_at}`).join('|'), [pages]);
  const indexSignatureRef = useRef(null);
  useEffect(() => {
    if (selectedPath.split('/').at(-1) !== 'index.md') return;
    if (indexSignatureRef.current !== null && indexSignatureRef.current !== pagesSignature) {
      loadPage(selectedPath);
    }
    indexSignatureRef.current = pagesSignature;
  }, [loadPage, pagesSignature, selectedPath]);

  useEffect(() => {
    if (viewingRevisionId) loadRevision(viewingRevisionId);
  }, [loadRevision, viewingRevisionId]);

  const selectPage = (path) => setParams(path === 'index.md' ? {} : { page: path });
  const revisionParams = (revisionId) => {
    const next = new URLSearchParams(params);
    if (revisionId) next.set('revision', revisionId);
    else next.delete('revision');
    return next;
  };
  const toggleRevision = (event, revisionId) => {
    event.preventDefault();
    setParams(revisionParams(viewingRevisionId === revisionId ? null : revisionId));
  };

  // The breadcrumb is the page heading, so the rendered body drops its leading H1; stored files keep it.
  const dropTitle = (text) => text.replace(/^\s*# [^\n]*\n*/, '');
  const isViewingRevision = Boolean(viewingRevisionId);
  const displayedDocument = isViewingRevision ? revision : page;
  const displayContent = displayedDocument
    ? dropTitle(stripYamlFrontmatter(displayedDocument.content ?? displayedDocument.body ?? ''))
    : '';
  // An open revision shows its own sources, not the current page's.
  const sources = frontMatterSources(displayedDocument?.front_matter);
  const missingPage = !isViewingRevision && !page && !pageRequest.error;
  const missingRevision = isViewingRevision && !revision && !revisionRequest.error;

  return (
    <div className="knowledge-pages-tab">
      <article>
        <header className="knowledge-page-header">
          <Breadcrumbs baseName={baseName || baseId} tree={tree} selectedPath={selectedPath} onSelect={selectPage} />
        </header>
        {pageRequest.error ? (
          <InlineFailure failure={pageRequest.error} operation="Load knowledge page" onRetry={() => loadPage(selectedPath)} />
        ) : null}
        {revisionsRequest.error ? (
          <InlineFailure failure={revisionsRequest.error} operation="Load page revisions" onRetry={() => loadRevisions(selectedPath)} />
        ) : null}
        {viewingRevisionId && revisionRequest.error ? (
          <InlineFailure failure={revisionRequest.error} operation="Load page revision" onRetry={() => loadRevision(viewingRevisionId)} />
        ) : null}
        {missingPage || missingRevision ? <p>Loading…</p> : displayedDocument ? (
          <>
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
                      <a
                        href={`?${revisionParams(viewingRevisionId === rev.revision_id ? null : rev.revision_id)}`}
                        aria-current={viewingRevisionId === rev.revision_id ? 'true' : undefined}
                        onClick={(event) => toggleRevision(event, rev.revision_id)}
                      >
                        {formatRevisionTime(rev)}
                      </a>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </>
        ) : null}
      </article>
    </div>
  );
}
