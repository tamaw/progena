import { useEffect } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { Navigate, useLocation, useNavigationType } from 'react-router-dom';
import { editorLocationForgotten, editorLocationRemembered } from '../store/editorDraftsSlice';

const EDITOR_PAGES = new Set([
  'chat', 'tasks', 'workflows', 'routines', 'agents', 'profiles',
  'instructions', 'schedules', 'knowledge', 'tools',
]);

/** Resolve remembered selection before mounting a page's default-item effects. */
export default function EditorNavigationBoundary({ children }) {
  const dispatch = useDispatch();
  const location = useLocation();
  const navigationType = useNavigationType();
  const segments = location.pathname.split('/').filter(Boolean);
  const page = EDITOR_PAGES.has(segments[0]) ? segments[0] : null;
  const bare = segments.length === 1;
  const remembered = useSelector((state) => page ? state.editorDrafts?.locations[page] : null);
  const current = `${location.pathname}${location.search}${location.hash}`;
  const explicitQuickChatHandoff = Boolean(location.state?.quickChatHandoff);
  // Delete/missing-item handlers deliberately replace with the collection URL.
  // Do not restore that invalid detail and send the page into a redirect loop.
  // A promoted quick chat is explicit intent and must mount its bootstrap page.
  const restoring = page && bare && navigationType !== 'REPLACE'
    && !explicitQuickChatHandoff && remembered;

  useEffect(() => {
    if (!page) return;
    if (bare && navigationType === 'REPLACE') {
      if (remembered) dispatch(editorLocationForgotten(page));
    } else if (!bare && remembered !== current) {
      dispatch(editorLocationRemembered({ page, location: current }));
    }
  }, [bare, current, dispatch, navigationType, page, remembered]);

  return restoring ? <Navigate to={remembered} replace /> : children;
}
