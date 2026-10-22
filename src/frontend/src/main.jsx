import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { Provider } from 'react-redux'
import 'highlight.js/styles/github-dark.css'
import App from './App.jsx'
import store from './store/store'
import { parseWorkspacePath, buildWorkspacePath, currentPageHost, ensureWorkspaceRegistered, getDefaultWorkspaceHost } from './lib/workspace'

// Every route lives at a plain path (/chat, /inbox, ...) when the active
// workspace is the host that served this page -- the default workspace
// needs no /w/ prefix, since the frontend's own host is what every
// unprefixed URL already resolves to. /w/<host>/... is reserved for an
// explicit *different* backend. Three cases on load, all real navigations
// (not client-side routes) that must resolve before the router (and its
// basename) ever mounts:
//   1. /w/<this page's host>/... -- redundant prefix, canonicalize to the
//      plain path.
//   2. /w/<other host>/... -- an explicit cross-host workspace link (e.g.
//      shared, or a previous switch); register and mount as-is.
//   3. a plain path -- the default workspace, unless the user previously
//      switched to a different backend and it's still their sticky
//      default, in which case redirect to that host's /w/ form.
const parsed = parseWorkspacePath(window.location.pathname)
const { pathname, search, hash } = window.location

function mount() {
  createRoot(document.getElementById('root')).render(
    <StrictMode>
      <Provider store={store}>
        <App />
      </Provider>
    </StrictMode>,
  )
}

if (parsed && parsed.host === currentPageHost()) {
  window.location.replace(parsed.rest + search + hash)
} else if (parsed) {
  ensureWorkspaceRegistered(parsed.host)
  mount()
} else {
  const defaultHost = getDefaultWorkspaceHost()
  if (defaultHost && defaultHost !== currentPageHost()) {
    window.location.replace(buildWorkspacePath(defaultHost, pathname) + search + hash)
  } else {
    ensureWorkspaceRegistered(currentPageHost())
    mount()
  }
}
