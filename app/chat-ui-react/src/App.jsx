import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ConfigProvider } from './context/ConfigContext';
import { NotificationProvider } from './context/NotificationContext';
import NotificationContainer from './components/common/NotificationContainer';
import ChatPage from './pages/ChatPage';
import DashboardPage from './pages/DashboardPage';
import AgentsPage from './pages/AgentsPage';
import LogsPage from './pages/LogsPage';

// Import styles
import './styles/main.css';
import './styles/chat.css';
import './styles/dashboard.css';
import './styles/agents.css';
import './styles/logs-page.css';
import './styles/notifications.css';
import './styles/status.css';

function App() {
  return (
    <ConfigProvider>
      <NotificationProvider>
        <BrowserRouter basename="/ui">
          <Routes>
            <Route path="/" element={<ChatPage />} />
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/agents" element={<AgentsPage />} />
            <Route path="/logs" element={<LogsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          <NotificationContainer />
        </BrowserRouter>
      </NotificationProvider>
    </ConfigProvider>
  );
}

export default App;
