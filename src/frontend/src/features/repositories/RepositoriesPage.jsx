import { useCallback, useEffect, useMemo, useState } from 'react';
import { dispatchQuickChatOpen, QUICK_CHAT_MODE } from '../../lib/browserContracts';

import { useDispatch, useSelector } from 'react-redux';
import {
  PaneList,
  PaneListFooter,
  PaneListFilters,
  PaneListToolbar,
  PaneListSearch,
  PaneListRows,
  PaneEmptyState,
} from '../../components/layout/PaneList';
import DetailHeader, {
  DeleteActionIcon,
  DetailHeaderAction,
  DetailHeaderActions,
  DetailStatus,
  OpenActionIcon,
  ReconnectActionIcon,
} from '../../components/layout/DetailHeader';
import PageShell from '../../components/layout/PageShell';
import DetailPane from '../../components/layout/DetailPane';
import PopupModal from '../../components/PopupModal';
import InlineFailure from '../../components/InlineFailure';
import useUiAction from '../../hooks/useUiAction';
import useConfirmAction from '../../hooks/useConfirmAction';
import useEntityDraft from '../../hooks/useEntityDraft';
import { entityDraftKey } from '../../store/editorDraftsSlice';
import { useConfig } from '../../context/ConfigContext';
import { selectRealtimeConnected } from '../../realtime/realtimeSlice';
import { deleteRepo, fetchRepoDetail, fetchRepoUpstream, fetchRepos, selectRepoDetail, selectRepos, selectReposState, selectWorkspaceSignal } from './state';
import { repoStatus } from './repositoriesPageModel';

const SOURCE_FILTERS = ['All', 'Remote', 'Local', 'Dirty'];

// Declared remote_url is stored bare (e.g. "github.com/tamaw/marketplace",
// no scheme) -- add https:// so window.open() navigates instead of resolving
// it relative to the current page.
function normalizeRemoteUrl(remoteUrl) {
  return /^[a-z][a-z0-9+.-]*:\/\//i.test(remoteUrl) ? remoteUrl : `https://${remoteUrl}`;
}

function repositoryMatchesFilter(row, activeFilter) {
  const isRemote = Boolean(row.declared?.remote_url);
  if (activeFilter === 'Remote') return isRemote;
  if (activeFilter === 'Local') return !isRemote;
  if (activeFilter === 'Dirty') return (row.scan?.dirty_count || 0) > 0;
  return true;
}

function groupRepositories(rows) {
  return rows.reduce((groups, row) => {
    const group = row.declared?.remote_url ? 'Remote sources' : 'Local paths';
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(row);
    return groups;
  }, new Map());
}

export default function RepositoriesPage() {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const reposUrl = getApiUrl('repos');
  const realtimeConnected = useSelector(selectRealtimeConnected);
  const rows = useSelector(selectRepos);
  const workspaceSignal = useSelector(selectWorkspaceSignal);

  const [activeFilter, setActiveFilter] = useState(SOURCE_FILTERS[0]);
  const [query, setQuery] = useState('');
  const [selectedId, setSelectedId] = useEntityDraft(entityDraftKey('view', 'repositories', 'selection'), null);

  const { loading, error: loadError } = useSelector(selectReposState);

  useEffect(() => {
    if (!realtimeConnected) return undefined;
    dispatch(fetchRepos({ reposUrl }));
    return undefined;
  }, [dispatch, realtimeConnected, reposUrl]);

  const filteredRows = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return rows.filter((row) => {
      const matchesFilter = repositoryMatchesFilter(row, activeFilter);
      if (!matchesFilter) return false;
      if (!normalizedQuery) return true;
      const haystack = `${row.declared?.name || ''} ${row.declared?.remote_url || ''} ${row.declared?.local_path || ''} ${row.declared?.owner || ''}`.toLowerCase();
      return haystack.includes(normalizedQuery);
    });
  }, [activeFilter, query, rows]);

  const groupedRows = useMemo(() => groupRepositories(filteredRows), [filteredRows]);
  const selectedRow = rows.find((row) => row.id === selectedId) || filteredRows[0] || rows[0] || null;
  const selectedRepoId = selectedRow?.id || null;

  const detail = useSelector((state) => selectRepoDetail(state, selectedRepoId));
  const fetchAction = useUiAction(`repository-fetch:${selectedRepoId || ''}`);
  const deleteAction = useConfirmAction(`repository-delete:${selectedRepoId || ''}`);

  useEffect(() => {
    if (!selectedRepoId) return;
    dispatch(fetchRepoDetail({ reposUrl, repoId: selectedRepoId }));
  }, [dispatch, reposUrl, selectedRepoId]);

  const openCreateChat = useCallback(() => {
    dispatchQuickChatOpen({ mode: QUICK_CHAT_MODE.REPOSITORY_CREATE });
  }, []);

  const handleDelete = useCallback(() => {
    if (!selectedRow) return;
    const repo = selectedRow;
    deleteAction.request('Remove repository', async ({ isCurrent }) => {
      await dispatch(deleteRepo({ reposUrl, repoId: repo.id })).unwrap();
      if (isCurrent()) setSelectedId(null);
    });
  }, [deleteAction, dispatch, reposUrl, selectedRow, setSelectedId]);

  const handleOpen = useCallback(() => {
    if (!selectedRow?.declared?.remote_url) return;
    window.open(normalizeRemoteUrl(selectedRow.declared.remote_url), '_blank', 'noopener,noreferrer');
  }, [selectedRow]);

  const handleFetch = useCallback(async () => {
    if (!selectedRow?.declared?.remote_url) return;
    await fetchAction.run('Fetch repository', async () => dispatch(
      fetchRepoUpstream({ reposUrl, repoId: selectedRow.id }),
    ).unwrap());
  }, [dispatch, fetchAction, reposUrl, selectedRow]);

  const status = repoStatus(selectedRow?.scan);
  const declared = selectedRow?.declared || {};
  const scan = detail?.scan || selectedRow?.scan || null;

  return (
    <PageShell className="repositories-page">
      <PaneList className="repositories-pane" label="Repositories">
        <PaneListFilters
          filters={SOURCE_FILTERS}
          activeFilter={activeFilter}
          onFilterChange={setActiveFilter}
          actionLabel="+ New"
          onAction={openCreateChat}
        />
        <PaneListToolbar>
          <PaneListSearch value={query} placeholder="Search sources" onChange={setQuery} />
        </PaneListToolbar>

        {loadError ? (
          <InlineFailure
            failure={loadError}
            operation="Load repositories"
            onRetry={() => dispatch(fetchRepos({ reposUrl }))}
          />
        ) : null}
        {loading && rows.length === 0 ? (
          <PaneEmptyState>Loading repositories…</PaneEmptyState>
        ) : rows.length === 0 && !loadError ? (
          <PaneEmptyState>No repositories declared.</PaneEmptyState>
        ) : filteredRows.length === 0 && !loadError ? (
          <PaneEmptyState>No repositories match the current filters.</PaneEmptyState>
        ) : (
          <PaneListRows
            groups={Array.from(groupedRows.entries()).map(([group, items]) => ({
              key: group,
              title: group,
              items,
            }))}
            getItemKey={(row) => row.id}
            renderItem={(row) => {
              const rowStatus = repoStatus(row.scan);
              return (
                <button
                  type="button"
                  className={`repo-source-row ${selectedRow?.id === row.id ? 'is-selected' : ''}`}
                  onClick={() => setSelectedId(row.id)}
                >
                  <span className={`repo-source-row__mark sess-row__dot--${rowStatus.tone}`} title={rowStatus.label} />
                  <span className="repo-source-row__title">{row.declared?.name}</span>
                  <span className="repo-source-row__age">{row.scan?.last_commit?.relative_time || '—'}</span>
                  <span className="repo-source-row__meta">{row.scan?.dirty_count ?? 0} uncommitted</span>
                </button>
              );
            }}
          />
        )}

        {selectedRow ? (
          <PaneListFooter>
            <span>{selectedRow.scan?.branch || '—'}</span>
            <span>{status.label}</span>
          </PaneListFooter>
        ) : null}
      </PaneList>

      <DetailPane className="repositories-main repo-profile">
        {!selectedRow ? (
          <div className="detail-pane__body">
            {!loadError ? <PaneEmptyState>Select a repository, or use + New to add one.</PaneEmptyState> : null}
          </div>
        ) : (
          <>
            <DetailHeader
              title={declared.name}
              actions={(
                <DetailHeaderActions>
                  <DetailHeaderAction
                    icon={<OpenActionIcon />}
                    label="Open"
                    tooltip={declared.remote_url ? `Open ${declared.remote_url}` : 'No remote URL configured'}
                    ariaLabel={`Open ${declared.name}`}
                    onClick={handleOpen}
                    disabled={!declared.remote_url}
                  />
                  <DetailHeaderAction
                    icon={<ReconnectActionIcon />}
                    label="Fetch"
                    tooltip={declared.remote_url ? 'Fetch from remote — check for upstream changes' : 'No remote URL configured'}
                    ariaLabel="Fetch from remote"
                    variant="primary"
                    onClick={handleFetch}
                    disabled={!declared.remote_url || fetchAction.busy}
                  />
                  <DetailHeaderAction
                    icon={<DeleteActionIcon />}
                    label="Remove"
                    tooltip="Remove declared repo"
                    ariaLabel="Remove declared repo"
                    onClick={handleDelete}
                    disabled={deleteAction.busy}
                  />
                </DetailHeaderActions>
              )}
              meta={[
                <DetailStatus key="state" tone={status.tone}>{status.label}</DetailStatus>,
                <span key="access" className="muted">{declared.access === 'write' ? 'read · write' : 'read only'}</span>,
                <span key="commits"><strong>{scan?.commit_count ?? '—'}</strong> commits</span>,
                <span key="branches"><strong>{scan?.branch_count ?? '—'}</strong> branches</span>,
              ]}
            />

            <div className="repo-profile__body">
              {fetchAction.error ? <InlineFailure failure={fetchAction.error} operation="Fetch repository" onRetry={handleFetch} /> : null}
              <section className="repo-stat-row" aria-label="Repository metrics">
                <article className="repo-stat-cell">
                  <div className="repo-stat-cell__label">Commits (30d)</div>
                  <div className="repo-stat-cell__value">{scan?.commits_30d ?? '—'}</div>
                  <div className="repo-stat-cell__detail">on {scan?.branch || 'the current branch'}</div>
                </article>
                <article className="repo-stat-cell">
                  <div className="repo-stat-cell__label">Uncommitted changes</div>
                  <div className="repo-stat-cell__value">{scan?.dirty_count ?? '—'}</div>
                  <div className="repo-stat-cell__detail">working tree</div>
                </article>
              </section>

              <section className="repo-section">
                <h2 className="repo-section__title">Workspace signal <span className="muted">{filteredRows.length} visible</span></h2>
                <div className="repo-stat-row repo-stat-row--compact">
                  <article className="repo-stat-cell">
                    <div className="repo-stat-cell__label">Linked repos</div>
                    <div className="repo-stat-cell__value">{workspaceSignal.linked_repos ?? rows.length}</div>
                  </article>
                  <article className="repo-stat-cell">
                    <div className="repo-stat-cell__label">Contributors</div>
                    <div className="repo-stat-cell__value">{workspaceSignal.contributor_count ?? '—'}</div>
                  </article>
                </div>
              </section>

              <section className="repo-section">
                <h2 className="repo-section__title">
                  Branches <span className="muted">{scan?.branch_count ?? 0}</span>
                </h2>
                {scan?.branches?.length ? (
                  <div className="repo-list">
                    {scan.branches.map((branch) => (
                      <article className="repo-branch" key={branch.name}>
                        <span className="repo-branch__icon">⎇</span>
                        <span className="repo-branch__name">
                          {branch.name} {branch.is_default ? <span className="muted">· default</span> : null}
                        </span>
                        <span className="repo-branch__ahead">
                          {branch.ahead != null ? `+${branch.ahead} / -${branch.behind}` : branch.sha}
                        </span>
                        <span className="repo-branch__author">{branch.author} · {branch.relative_time}</span>
                      </article>
                    ))}
                  </div>
                ) : (
                  <PaneEmptyState>
                    {scan?.exists ? 'No branches found.' : 'Not yet cloned at the declared local path — no git state to show.'}
                  </PaneEmptyState>
                )}
              </section>

              <section className="repo-section">
                <h2 className="repo-section__title">Recent commits <span className="muted">{scan?.branch || ''}</span></h2>
                {scan?.commits?.length ? (
                  <div className="repo-list">
                    {scan.commits.map((commit) => (
                      <article className="repo-commit" key={commit.sha}>
                        <span className="repo-commit__message">{commit.message}</span>
                        <span className="repo-commit__sha">{commit.sha}</span>
                        <span className="repo-commit__time">{commit.relative_time}</span>
                      </article>
                    ))}
                  </div>
                ) : (
                  <PaneEmptyState>No commit history to show.</PaneEmptyState>
                )}
              </section>

            </div>
          </>
        )}
      </DetailPane>
      {deleteAction.open ? (
        <PopupModal
          eyebrow="Remove repository"
          title={`Remove “${declared.name}”?`}
          message="This removes the repository declaration. It does not delete the local worktree."
          error={deleteAction.error}
          confirmLabel="Remove repository"
          busyLabel="Removing…"
          onClose={deleteAction.close}
          onConfirm={deleteAction.confirm}
          busy={deleteAction.busy}
        />
      ) : null}
    </PageShell>
  );
}
