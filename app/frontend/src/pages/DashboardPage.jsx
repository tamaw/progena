import { useState, useEffect, useCallback } from 'react';
import { useApi } from '../hooks/useApi';
import { useStatusSSE } from '../hooks/useSSE';
import { useHealth } from '../context/HealthContext';
import Header from '../components/layout/Header';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  ArcElement,
  Title,
  Tooltip,
  Legend,
  Filler
} from 'chart.js';
import { Line, Doughnut, Bar } from 'react-chartjs-2';

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  ArcElement,
  Title,
  Tooltip,
  Legend,
  Filler
);

function DashboardPage() {
  const { isHealthy } = useHealth();
  const { connectionState } = useApi();
  const [status, setStatus] = useState(null);

  const handleStatusEvent = useCallback((event) => {
    if (!event?.data) return;

    if (event.type === 'connected' || event.type === 'status' || event.type === 'heartbeat') {
      setStatus(event.data);
    }
  }, []);

  // Use status-only SSE stream for dashboard
  useStatusSSE(handleStatusEvent);

  const formatUptime = (seconds) => {
    if (!seconds) return '--';
    if (seconds < 60) return `${Math.floor(seconds)}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  };

  const formatNumber = (num) => {
    if (!num) return '0';
    if (num >= 1000000) return `${(num / 1000000).toFixed(1)}M`;
    if (num >= 1000) return `${(num / 1000).toFixed(1)}K`;
    return num.toString();
  };

  const tokenChartData = {
    labels: tokenHistory.map(h => h.time),
    datasets: [
      {
        label: 'Input Tokens',
        data: tokenHistory.map(h => h.input),
        borderColor: 'rgb(88, 166, 255)',
        backgroundColor: 'rgba(88, 166, 255, 0.1)',
        fill: true,
        tension: 0.4
      },
      {
        label: 'Output Tokens',
        data: tokenHistory.map(h => h.output),
        borderColor: 'rgb(63, 185, 80)',
        backgroundColor: 'rgba(63, 185, 80, 0.1)',
        fill: true,
        tension: 0.4
      }
    ]
  };

  const tokenBreakdownData = {
    labels: ['Input', 'Output', 'Cached'],
    datasets: [{
      data: [
        metrics?.tokens?.input || 0,
        metrics?.tokens?.output || 0,
        metrics?.tokens?.cached || 0
      ],
      backgroundColor: [
        'rgb(88, 166, 255)',
        'rgb(63, 185, 80)',
        'rgb(210, 153, 34)'
      ]
    }]
  };

  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        display: false
      }
    },
    scales: {
      y: {
        beginAtZero: true,
        grid: {
          color: 'rgba(255, 255, 255, 0.1)'
        },
        ticks: {
          color: 'rgba(255, 255, 255, 0.6)'
        }
      },
      x: {
        grid: {
          display: false
        },
        ticks: {
          color: 'rgba(255, 255, 255, 0.6)',
          maxTicksLimit: 6
        }
      }
    }
  };

  const doughnutOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        position: 'bottom',
        labels: {
          color: 'rgba(255, 255, 255, 0.8)'
        }
      }
    }
  };

  return (
    <div className="dashboard-container">
      <Header connectionState={connectionState} />
      <main className="dashboard-main">
        {/* Stats Grid */}
        <section className="stats-grid">
          <div className="stat-card primary">
            <div className="stat-icon">🧠</div>
            <div className="stat-content">
              <div className="stat-value">{status?.progenitor_running ? 'Running' : 'Stopped'}</div>
              <div className="stat-label">Agent Status</div>
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-icon">🔄</div>
            <div className="stat-content">
              <div className="stat-value">{formatNumber(status?.cycle_count)}</div>
              <div className="stat-label">Total Cycles</div>
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-icon">💬</div>
            <div className="stat-content">
              <div className="stat-value">{formatNumber(metrics?.requests?.total)}</div>
              <div className="stat-label">Requests</div>
            </div>
          </div>
          <div className="stat-card token-card">
            <div className="stat-icon">🎯</div>
            <div className="stat-content">
              <div className="stat-value">{formatNumber((metrics?.tokens?.input || 0) + (metrics?.tokens?.output || 0))}</div>
              <div className="stat-label">Total Tokens</div>
              <div className="stat-breakdown">
                <span className="token-input">↓{formatNumber(metrics?.tokens?.input)}</span>
                <span className="token-output">↑{formatNumber(metrics?.tokens?.output)}</span>
              </div>
            </div>
          </div>
          <div className="stat-card cost-card">
            <div className="stat-icon">💰</div>
            <div className="stat-content">
              <div className="stat-value">${(metrics?.cost?.total || 0).toFixed(2)}</div>
              <div className="stat-label">Est. Cost</div>
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-icon">⏱️</div>
            <div className="stat-content">
              <div className="stat-value">{formatUptime(status?.uptime_seconds)}</div>
              <div className="stat-label">Uptime</div>
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-icon">💾</div>
            <div className="stat-content">
              <div className="stat-value">{status?.memory_mb?.toFixed(1) || '--'} MB</div>
              <div className="stat-label">Memory</div>
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-icon">📊</div>
            <div className="stat-content">
              <div className="stat-value">{metrics?.requests?.per_minute?.toFixed(1) || '0'}</div>
              <div className="stat-label">Requests/min</div>
            </div>
          </div>
        </section>

        {/* Charts Row */}
        <section className="charts-row">
          <div className="chart-card large">
            <div className="chart-header">
              <h3>📈 Token Consumption Over Time</h3>
            </div>
            <div className="chart-body">
              <Line data={tokenChartData} options={chartOptions} />
            </div>
          </div>
          <div className="chart-card">
            <div className="chart-header">
              <h3>🎯 Token Breakdown</h3>
            </div>
            <div className="chart-body">
              <Doughnut data={tokenBreakdownData} options={doughnutOptions} />
            </div>
          </div>
        </section>

        {/* Bottom Panels */}
        <section className="dashboard-grid">
          <div className="dashboard-panel agents-panel">
            <div className="panel-header">
              <h3>🤖 Registered Agents</h3>
              <span className="badge">{status?.agents?.length || 0}</span>
            </div>
            <div className="panel-body">
              {status?.agents?.length > 0 ? (
                status.agents.map((agent, i) => (
                  <div key={i} className="agent-item">
                    <span className="agent-icon">{agent.icon || '🤖'}</span>
                    <span className="agent-name">{agent.name}</span>
                    <span className={`agent-status ${agent.status}`}>{agent.status}</span>
                  </div>
                ))
              ) : (
                <div className="empty-state">
                  <span className="empty-icon">🤖</span>
                  <span>No agents registered</span>
                </div>
              )}
            </div>
          </div>

          <div className="dashboard-panel activity-panel">
            <div className="panel-header">
              <h3>📋 Activity Timeline</h3>
            </div>
            <div className="panel-body timeline">
              <div className="empty-state">
                <span className="empty-icon">📋</span>
                <span>No activity yet</span>
              </div>
            </div>
          </div>
        </section>
      </main>

      <footer className="dashboard-footer">
        <div className="footer-info">
          <span>Progenitor v{status?.version || '0.1.0'}</span>
          <span className="separator">•</span>
          <span>PID: {status?.pid || '--'}</span>
        </div>
        <div className="footer-status">
          <span className="pulse-dot"></span>
          <span>Last update: {new Date().toLocaleTimeString()}</span>
        </div>
      </footer>
    </div>
  );
}

export default DashboardPage;
