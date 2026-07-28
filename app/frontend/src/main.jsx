import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { Provider } from 'react-redux'
import 'highlight.js/styles/github-dark.css'
import App from './App.jsx'
import store from './store/store'
import { parseWorkspacePath, buildWorkspacePath, currentPageHost, ensureWorkspaceRegistered, getDefaultWorkspaceHost } from './lib/workspace'

// Every route lives under /w/<host>/... . A bare / (or any unprefixed deep
// link) redirects to the default workspace: the last one the user
// deliberately switched to, or -- on a first-ever visit -- whatever host
// served this page. This is a real navigation, not a client-side route --
// it must happen before the router (and its basename) ever mounts.
if (!parseWorkspacePath(window.location.pathname)) {
  const { pathname, search, hash } = window.location
  window.location.replace(buildWorkspacePath(getDefaultWorkspaceHost() || currentPageHost(), pathname) + search + hash)
} else {
  ensureWorkspaceRegistered(parseWorkspacePath(window.location.pathname).host)

  createRoot(document.getElementById('root')).render(
    <StrictMode>
      <Provider store={store}>
        <App />
      </Provider>
    </StrictMode>,
  )
}
