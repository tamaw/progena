import { useEffect, useMemo, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useNavigate, useParams } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import PageShell from '../components/layout/PageShell';
import DetailPane from '../components/layout/DetailPane';
import {
  PaneCollapseSeam,
  PaneEmptyState,
  PaneExpandRail,
  PaneListFilters,
  PaneListFooter,
  PaneListRows,
  PaneListSearch,
  PaneListToolbar,
  PaneSection,
} from '../components/layout/PaneList';
import { usePaneCollapse } from '../components/layout/usePaneCollapse';
import InlineFailure from '../components/common/InlineFailure';
import useMissingEntityRoute from '../hooks/useMissingEntityRoute';
import { formatCompactRelativeAge } from '../lib/displayTime';
import { selectRealtime } from '../store/realtimeSlice';
import { fetchModules, selectModules, selectModulesState } from '../store/uiDataSlice';
import ModulePage from './ModulePage';
import {
  MODULE_FILTERS, MODULE_STATE_LABEL, MODULE_STATE_TONE, filterModules, moduleState,
} from './modulesPageModel';

function ModuleRow({ module, selected, onSelect }) {
  const state = moduleState(module);
  return (
    <button
      type="button"
      className={`sess-row agents-agent-row ${selected ? 'is-selected' : ''}`}
      onClick={() => onSelect(module.name)}
    >
      <span className="sess-row__top">
        <span className={`sess-row__dot sess-row__dot--${MODULE_STATE_TONE[state]}`} title={MODULE_STATE_LABEL[state]} />
        <span className="sess-row__agent agents-agent-row__name">{module.title || module.name}</span>
      </span>
      <span className="sess-row__age">{module.updated_at ? formatCompactRelativeAge(module.updated_at) : ''}</span>
      <span className="sess-row__title">{module.description || 'No description'}</span>
      <span className="sess-row__msgs">
        <span><span className="num">{module.files?.length || 0}</span> files</span>
        {module.libraries?.length ? <span>{module.libraries.join(', ')}</span> : null}
      </span>
    </button>
  );
}

const openModuleChat = (draft) => window.dispatchEvent(
  new CustomEvent('progenitor:open-quick-chat', { detail: { draft } }),
);

export default function ModulesPage() {
  const { name } = useParams();
  const dispatch = useDispatch();
  const navigate = useNavigate();
  const { getApiUrl } = useConfig();
  const modulesUrl = getApiUrl('modules');
  const realtime = useSelector(selectRealtime);
  const modules = useSelector(selectModules);
  const state = useSelector(selectModulesState);
  const { collapsed, setCollapsed, paneClassName } = usePaneCollapse('tasks-list');
  const [filter, setFilter] = useState('All');
  const [query, setQuery] = useState('');

  useEffect(() => {
    if (realtime.connected) dispatch(fetchModules({ modulesUrl }));
  }, [dispatch, modulesUrl, realtime.connected]);

  useMissingEntityRoute({
    hydrated: state.loaded, entityId: name, entities: modules, getEntityId: (item) => item.name, fallbackPath: '/modules',
  });

  // Like the other list/detail pages, land on the first module when none is selected.
  useEffect(() => {
    if (!name && modules.length) navigate(`/modules/${encodeURIComponent(modules[0].name)}`, { replace: true });
  }, [modules, name, navigate]);

  const visible = useMemo(() => filterModules(modules, { filter, query }), [filter, modules, query]);
  const errorCount = modules.filter((module) => moduleState(module) !== 'available').length;
  const select = (moduleName) => navigate(`/modules/${encodeURIComponent(moduleName)}`);
  const selected = name ? modules.find((module) => module.name === name) : null;

  return (
    <PageShell className="tasks-page modules-page">
      <PaneSection className={paneClassName} aria-label="Modules" onItemSelect={() => setCollapsed(true)}>
        {collapsed ? (
          <PaneExpandRail label="Modules" onExpand={() => setCollapsed(false)} />
        ) : (
          <>
            <PaneCollapseSeam label="Modules" onCollapse={() => setCollapsed(true)} />
            <button type="button" className="pane-mobile-scrim" aria-label="Close modules list" onClick={() => setCollapsed(true)} />
            <PaneListFilters
              filters={MODULE_FILTERS}
              activeFilter={filter}
              onFilterChange={setFilter}
              actionLabel="+ New"
              onAction={() => openModuleChat('Build me a module that ')}
            />
            <PaneListToolbar>
              <PaneListSearch value={query} placeholder="Search modules" onChange={setQuery} />
            </PaneListToolbar>
            {state.error ? (
              <InlineFailure failure={state.error} operation="Load modules" onRetry={() => dispatch(fetchModules({ modulesUrl }))} />
            ) : !state.loaded ? (
              <PaneEmptyState>Loading modules…</PaneEmptyState>
            ) : !visible.length ? (
              <PaneEmptyState>{modules.length ? 'No matching modules.' : 'No modules yet.'}</PaneEmptyState>
            ) : (
              <PaneListRows
                items={visible}
                getItemKey={(module) => module.name}
                renderItem={(module) => <ModuleRow module={module} selected={module.name === name} onSelect={select} />}
              />
            )}
            <PaneListFooter>
              <span><span className="num">{modules.length}</span> modules</span>
              <span>{errorCount ? `${errorCount} need attention` : 'all available'}</span>
            </PaneListFooter>
          </>
        )}
      </PaneSection>

      <DetailPane flush className="task-detail modules-detail">
        {selected ? (
          <ModulePage key={selected.name} name={selected.name} onAskProgenitor={openModuleChat} />
        ) : (
          <PaneEmptyState detail>
            {state.loaded && !modules.length
              ? 'Ask Progenitor in chat for a dashboard, diagram or game; it appears here as soon as it is written.'
              : 'Select a module.'}
          </PaneEmptyState>
        )}
      </DetailPane>
    </PageShell>
  );
}
