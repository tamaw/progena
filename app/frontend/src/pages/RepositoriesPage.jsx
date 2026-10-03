import { useCallback, useEffect, useMemo, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import {
  PaneList,
  PaneListFooter,
  PaneListFilters,
  PaneListToolbar,
  PaneListSearch,
  PaneListRows,
  PaneEmptyState,
} from '../components/layout/PaneList';
import DetailHeader, {
  DeleteActionIcon,
  DetailHeaderAction,
  DetailHeaderActions,
  DetailStatus,
  OpenActionIcon,
  ReconnectActionIcon,
} from '../components/layout/DetailHeader';
import PageShell from '../components/layout/PageShell';
import DetailPane from '../components/layout/DetailPane';
import { useConfig } from '../context/ConfigContext';
import { selectRealtimeConnected } from '../store/realtimeSlice';
import {
  createRepo,
  deleteRepo,
  fetchRepoDetail,
  fetchRepoUpstream,
  fetchRepos,
  selectRepoDetail,
  selectRepos,
  selectReposState,
  selectWorkspaceSignal,
} from '../store/uiDataSlice';

const SOURCE_FILTERS = ['All', 'Remote', 'Local', 'Dirty'];

const ACCESS_OPTIONS = [
  { value: 'read', label: 'Read only' },
  { value: 'write', label: 'Read / write' },
];

// A repo's row status is entirely derived from its latest heartbeat scan —
// there is no PR/CI signal available (that needs a GitHub API integration
// this pass deliberately doesn't add).
function scanStatus(scan) {
  if (!scan || !scan.exists) return 'pending';
  if (!scan.is_git_repo) return 'error';
  if (scan.error) return 'error';
  if ((scan.dirty_count || 0) > 0) return 'dirty';
  if ((scan.upstream_ahead || 0) > 0 || (scan.upstream_behind || 0) > 0) return 'upstream';
  return 'clean';
}

const REPO_STATUS_TONE = {
  clean: 'muted',
  upstream: 'ok',
  dirty: 'warning',
  error: 'error',
  pending: 'muted',
};

const REPO_STATUS_LABEL = {
  clean: 'Synced',
  upstream: 'Changes upstream',
  dirty: 'Uncommitted changes',
  error: 'Scan error',
  pending: 'Not yet cloned',
};

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

function RepoCreateForm({ onCancel, onCreate, submitting, error }) {
  const [name, setName] = useState('');
  const [localPath, setLocalPath] = useState('');
  const [access, setAccess] = useState('read');
  const [remoteUrl, setRemoteUrl] = useState('');
  const [owner, setOwner] = useState('');

  const submit = (event) => {
    event.preventDefault();
    onCreate({ name, localPath, access, remoteUrl, owner });
  };

  return (
    <form className="repo-create-form" onSubmit={submit}>
      <label className="repo-create-form__field">
        <span>Name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="progenitor" required />
      </label>
      <label className="repo-create-form__field">
        <span>Local worktree path</span>
        <input
          value={localPath}
          onChange={(e) => setLocalPath(e.target.value)}
          placeholder="progenitor"
          required
        />
      </label>
      <label className="repo-create-form__field">
        <span>Remote URL (optional)</span>
        <input
          value={remoteUrl}
          onChange={(e) => setRemoteUrl(e.target.value)}
          placeholder="github.com/tamaw/progenitor"
        />
      </label>
      <label className="repo-create-form__field">
        <span>Owner (optional)</span>
        <input value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="Progenitor" />
      </label>
      <label className="repo-create-form__field">
        <span>Access</span>
        <select value={access} onChange={(e) => setAccess(e.target.value)}>
          {ACCESS_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
      </label>
      {error ? <div className="repo-create-form__error">{error}</div> : null}
      <div className="repo-create-form__actions">
        <button type="button" className="repo-btn repo-btn--ghost" onClick={onCancel} disabled={submitting}>
          Cancel
        </button>
        <button type="submit" className="repo-btn repo-btn--primary" disabled={submitting}>
          {submitting ? 'Adding…' : 'Add repository'}
        </button>
      </div>
    </form>
  );
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
  const [selectedId, setSelectedId] = useState(null);
  const [isCreating, setIsCreating] = useState(false);
  const [createError, setCreateError] = useState(null);
  const [creating, setCreating] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [fetchError, setFetchError] = useState(null);

  const { loading } = useSelector(selectReposState);

  useEffect(() => {
    if (!realtimeConnected) return undefined;
    dispatch(fetchRepos({ reposUrl })).unwrap().catch(() => {
      // Failure is surfaced via repos.error in the store; nothing local to reset.
    });
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

  useEffect(() => {
    if (!selectedRepoId) return;
    dispatch(fetchRepoDetail({ reposUrl, repoId: selectedRepoId }));
  }, [dispatch, reposUrl, selectedRepoId]);

  useEffect(() => {
    setFetchError(null);
  }, [selectedRepoId]);

  const handleCreate = useCallback(async ({ name, localPath, access, remoteUrl, owner }) => {
    if (!name.trim() || !localPath.trim()) {
      setCreateError('Name and local worktree path are required.');
      return;
    }
    setCreating(true);
    setCreateError(null);
    try {
      await dispatch(createRepo({
        reposUrl,
        name,
        localPath,
        access,
        remoteUrl,
        owner,
      })).unwrap();
      // The response is just an ack; the created entry arrives on the next
      // `repositories.snapshot` broadcast, same as every other CRUD flow here.
      setSelectedId(null);
      setIsCreating(false);
    } catch (err) {
      setCreateError(err?.message || 'Failed to add repository.');
    } finally {
      setCreating(false);
    }
  }, [dispatch, reposUrl]);

  const handleDelete = useCallback(() => {
    if (!selectedRow) return;
    dispatch(deleteRepo({ reposUrl, repoId: selectedRow.id }));
    setSelectedId(null);
  }, [dispatch, reposUrl, selectedRow]);

  const handleOpen = useCallback(() => {
    if (!selectedRow?.declared?.remote_url) return;
    window.open(normalizeRemoteUrl(selectedRow.declared.remote_url), '_blank', 'noopener,noreferrer');
  }, [selectedRow]);

  const handleFetch = useCallback(async () => {
    if (!selectedRow?.declared?.remote_url) return;
    setFetching(true);
    setFetchError(null);
    try {
      await dispatch(fetchRepoUpstream({ reposUrl, repoId: selectedRow.id })).unwrap();
      // Just an ack; the refreshed branch/upstream state arrives on the next
      // `repositories.snapshot` + `repo.detail.updated` broadcast.
    } catch (err) {
      setFetchError(err?.message || 'Fetch failed.');
    } finally {
      setFetching(false);
    }
  }, [dispatch, reposUrl, selectedRow]);

  const status = scanStatus(selectedRow?.scan);
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
          onAction={() => setIsCreating(true)}
        />
        <PaneListToolbar>
          <PaneListSearch value={query} placeholder="Search sources" onChange={setQuery} />
        </PaneListToolbar>

        {isCreating ? (
          <RepoCreateForm
            onCancel={() => { setIsCreating(false); setCreateError(null); }}
            onCreate={handleCreate}
            submitting={creating}
            error={createError}
          />
        ) : loading && rows.length === 0 ? (
          <PaneEmptyState>Loading repositories…</PaneEmptyState>
        ) : rows.length === 0 ? (
          <PaneEmptyState>No repositories declared.</PaneEmptyState>
        ) : filteredRows.length === 0 ? (
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
              const rowStatus = scanStatus(row.scan);
              return (
                <button
                  type="button"
                  className={`repo-source-row ${selectedRow?.id === row.id ? 'is-selected' : ''}`}
                  onClick={() => setSelectedId(row.id)}
                >
                  <span className={`repo-source-row__mark repo-source-row__mark--${REPO_STATUS_TONE[rowStatus]}`} />
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
            <span>{REPO_STATUS_LABEL[status]}</span>
          </PaneListFooter>
        ) : null}
      </PaneList>

      <DetailPane className="repositories-main repo-profile">
        {!selectedRow ? (
          <div className="detail-pane__body">
            <PaneEmptyState>Select a repository, or use + New to add one.</PaneEmptyState>
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
                    disabled={!declared.remote_url || fetching}
                  />
                  <DetailHeaderAction icon={<DeleteActionIcon />} label="Remove" tooltip="Remove declared repo" ariaLabel="Remove declared repo" onClick={handleDelete} />
                </DetailHeaderActions>
              )}
              meta={[
                <DetailStatus key="state" tone={REPO_STATUS_TONE[status]}>{REPO_STATUS_LABEL[status]}</DetailStatus>,
                <span key="access" className="muted">{declared.access === 'write' ? 'read · write' : 'read only'}</span>,
                <span key="commits"><strong>{scan?.commit_count ?? '—'}</strong> commits</span>,
                <span key="branches"><strong>{scan?.branch_count ?? '—'}</strong> branches</span>,
                fetchError ? <DetailStatus key="fetch-error" tone="error">{fetchError}</DetailStatus> : null,
              ]}
            />

            <div className="repo-profile__body">
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
    </PageShell>
  );
}
