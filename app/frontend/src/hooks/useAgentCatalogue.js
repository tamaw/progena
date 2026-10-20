import { useCallback, useEffect, useMemo, useRef } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useNavigate, useParams } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import { failureMessage } from '../lib/failures';
import { selectRealtimeConnected, selectRealtimeStreamReady } from '../store/realtimeSlice';
import {
  fetchAgentProfiles,
  requestAgentDetail,
  selectActiveAgentProfile,
  selectActiveAgentProfileState,
  selectAgentProfiles,
  selectAgentProfilesState,
} from '../store/uiDataSlice';

const CATALOGUE_KIND = {
  agents: (agent) => agent.kind !== 'routine',
  routines: (agent) => agent.kind === 'routine',
};

/**
 * Shared route-driven roster and SSE detail lifecycle for Agents and Routines.
 * The page still owns its domain-specific filters, drafts, and mutations.
 */
export default function useAgentCatalogue({ kind, routeBase, detailSuffix = '' }) {
  const dispatch = useDispatch();
  const navigate = useNavigate();
  const { agentId: routeAgentId } = useParams();
  const { getApiUrl } = useConfig();
  const agentsUrl = getApiUrl('agentProfiles');
  const realtimeConnected = useSelector(selectRealtimeConnected);
  const streamReady = useSelector(selectRealtimeStreamReady);
  const fetchedAgents = useSelector(selectAgentProfiles);
  const agentsState = useSelector(selectAgentProfilesState);
  const activeAgent = useSelector(selectActiveAgentProfile);
  const activeAgentState = useSelector(selectActiveAgentProfileState);
  const requestedListRef = useRef(null);

  const agents = useMemo(() => fetchedAgents.filter(CATALOGUE_KIND[kind]), [fetchedAgents, kind]);
  const selectedAgentId = routeAgentId ?? null;
  const effectiveAgentId = selectedAgentId ?? agents[0]?.id ?? null;
  const makePath = useCallback((id) => `${routeBase}/${encodeURIComponent(id)}${detailSuffix}`, [detailSuffix, routeBase]);

  const loadRoster = useCallback((retry = false) => {
    if (!realtimeConnected) return;
    if (!retry && requestedListRef.current === agentsUrl) return;
    requestedListRef.current = agentsUrl;
    dispatch(fetchAgentProfiles({ agentsUrl }));
  }, [agentsUrl, dispatch, realtimeConnected]);

  useEffect(() => {
    if (!realtimeConnected) {
      requestedListRef.current = null;
      return;
    }
    loadRoster();
  }, [loadRoster, realtimeConnected]);

  const selectAgent = useCallback((id, { replace = false } = {}) => {
    navigate(makePath(id), { replace });
  }, [makePath, navigate]);

  useEffect(() => {
    if (!effectiveAgentId) return;
    const target = makePath(effectiveAgentId);
    if (window.location.pathname !== target) navigate(target, { replace: true });
  }, [effectiveAgentId, makePath, navigate]);

  const loadDetail = useCallback(() => {
    if (!effectiveAgentId || !streamReady) return;
    dispatch(requestAgentDetail({ agentsUrl, agentId: effectiveAgentId }));
  }, [agentsUrl, dispatch, effectiveAgentId, streamReady]);

  useEffect(() => {
    if (!effectiveAgentId || !streamReady || activeAgent?.id === effectiveAgentId) return;
    loadDetail();
  }, [activeAgent?.id, effectiveAgentId, loadDetail, streamReady]);

  const listAgent = useMemo(
    () => agents.find((agent) => agent.id === effectiveAgentId) || null,
    [agents, effectiveAgentId],
  );
  // A detail failure belongs only to the requested route identity. The roster
  // remains usable, and callers can render the cached row beside the retry.
  const detailFailure = activeAgentState.error && activeAgentState.agentId === effectiveAgentId
    ? failureMessage(activeAgentState.error, `Load ${kind === 'agents' ? 'agent' : 'routine'} details`)
    : null;
  const rosterFailure = agentsState.error ? failureMessage(agentsState.error, `Load ${kind}`) : null;
  const retryRoster = useCallback(() => loadRoster(true), [loadRoster]);
  const selectedAgent = activeAgent?.id === effectiveAgentId ? activeAgent : listAgent;

  return {
    agents,
    agentsUrl,
    agentsState,
    effectiveAgentId,
    selectedAgent,
    rosterFailure,
    detailFailure,
    detailPending: !detailFailure && (!activeAgent || activeAgent.id !== effectiveAgentId),
    selectAgent,
    retryRoster,
    retryDetail: loadDetail,
  };
}
