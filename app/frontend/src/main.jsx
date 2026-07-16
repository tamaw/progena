import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { Provider } from 'react-redux'
import 'highlight.js/styles/github-dark.css'
import App from './App.jsx'
import store from './store/store'
import { initializeChatPerformanceTrace } from './components/chat/chatPerformanceTrace.js'

// The initial navigation query is the sole trace activation contract.
initializeChatPerformanceTrace()

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <Provider store={store}>
      <App />
    </Provider>
  </StrictMode>,
)
