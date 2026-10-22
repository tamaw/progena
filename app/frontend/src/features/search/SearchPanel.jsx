import { useEffect, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { NavLink } from 'react-router-dom';
import { searchAll, selectSearchResults } from './state';
import { useConfig } from '../../context/ConfigContext';
import { SearchIcon } from '../../components/navIcons';
import { TopNavPane } from '../../components/panels/PanelFrame';
import { normalizeSearchText } from './searchModel';

export function SearchPanel({ query, navigationItems, onClose, isPinned, onTogglePin, isOpen }) {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const [search, setSearch] = useState(query);
  const [activeQuery, setActiveQuery] = useState('');
  // A rejected request (network/non-202) for a query; no results event will come.
  const [requestError, setRequestError] = useState(null);
  // The backend matches literal text, so it gets the query as typed (trimmed);
  // the punctuation-insensitive form is only for matching sidebar pages here.
  const backendQuery = search.trim();
  const normalizedSearch = normalizeSearchText(search);
  const searchData = useSelector((state) => selectSearchResults(state, activeQuery));

  useEffect(() => {
    if (backendQuery.length < 3) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- clearing search results when query is too short
      setActiveQuery('');
      return;
    }
    const timer = setTimeout(() => {
      setActiveQuery(backendQuery);
      setRequestError(null);
      dispatch(searchAll({ searchUrl: getApiUrl('/api/ui/search'), query: backendQuery }))
        .unwrap()
        .catch((error) => setRequestError({ query: backendQuery, message: error?.message || 'Search failed' }));
    }, 300);
    return () => clearTimeout(timer);
  }, [backendQuery, dispatch, getApiUrl]);

  const routeResults = navigationItems
    .filter((item) => normalizeSearchText(`${item.label} ${item.to}`).includes(normalizedSearch))
    .map((item) => ({
      id: `route:${item.to}`,
      title: item.label,
      subtitle: item.to === '/chat' ? 'Open chat workspace' : `Open ${item.to}`,
      to: item.to,
    }));

  const failedRequest = requestError && requestError.query === backendQuery && !searchData ? requestError.message : null;
  const backendSections = searchData && Array.isArray(searchData.sections) ? searchData.sections : [];
  const isSearching = backendQuery.length >= 3 && !failedRequest && (activeQuery !== backendQuery || !searchData);
  const hasAnyResults = routeResults.length > 0 || backendSections.some((s) => s.results && s.results.length > 0);
  const hasAnyError = Boolean(failedRequest) || backendSections.some((s) => s.error);

  return (
    <TopNavPane title="Search" className="topnav-pane--search" onClose={onClose} isPinned={isPinned} onTogglePin={onTogglePin} isOpen={isOpen}>
      <div className="chrome-search-panel">
        <div className="chrome-search-panel__input">
          <span aria-hidden="true"><SearchIcon /></span>
          <input
            autoFocus
            value={search}
            placeholder="Search"
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        {normalizedSearch ? (
          <div className="topnav-list chrome-search-results">
            {!hasAnyResults && !isSearching && !hasAnyError ? (
              <div className="chrome-empty">No matches.</div>
            ) : null}
            {isSearching ? <div className="chrome-search-loading muted">Searching…</div> : null}
            {failedRequest ? <div className="chrome-search-error muted">{failedRequest}</div> : null}
            {routeResults.length > 0 ? (
              <div className="chrome-search-group">
                <div className="chrome-search-group__label">Pages</div>
                {routeResults.map((result) => (
                  <NavLink key={result.id} to={result.to} className="topnav-list-item chrome-search-result" onClick={onClose}>
                    <span className="topnav-list-item__dot" />
                    <span>
                      <strong>{result.title}</strong>
                      <small>{result.subtitle}</small>
                    </span>
                  </NavLink>
                ))}
              </div>
            ) : null}
            {backendSections.map((section) => (
              section.results && section.results.length > 0 ? (
                <div key={section.id} className="chrome-search-group">
                  <div className="chrome-search-group__label">{section.label}</div>
                  {section.results.map((result) => (
                    <NavLink key={`${section.id}:${result.id}`} to={result.to} className="topnav-list-item chrome-search-result" onClick={onClose}>
                      <span className="topnav-list-item__dot" />
                      <span>
                        <strong>{result.title}</strong>
                        <small>{result.subtitle}</small>
                      </span>
                    </NavLink>
                  ))}
                  {section.error ? (
                    <div className="chrome-search-error muted">{section.error}</div>
                  ) : null}
                </div>
              ) : section.error ? (
                <div key={section.id} className="chrome-search-group">
                  <div className="chrome-search-group__label">{section.label}</div>
                  <div className="chrome-search-error muted">{section.error}</div>
                </div>
              ) : null
            ))}
          </div>
        ) : null}
      </div>
    </TopNavPane>
  );
}
