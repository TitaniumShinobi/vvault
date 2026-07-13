import React, { useEffect, useState } from 'react';

const Dashboard = ({ systemInfo }) => {
  const [stats, setStats] = useState({
    totalCapsules: 0,
    systemUptime: '',
    backendStatus: 'connecting',
    lastSync: 'Never'
  });

  useEffect(() => {
    if (systemInfo) {
      setStats({
        totalCapsules: systemInfo.capsules_loaded || 0,
        systemUptime: formatUptime(systemInfo.uptime_seconds || 0),
        backendStatus: systemInfo.system_status || 'unknown',
        lastSync: formatDateTime(systemInfo.server_started)
      });
    }
  }, [systemInfo]);

  const formatUptime = (seconds) => {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);
    return `${hours}h ${minutes}m ${secs}s`;
  };

  const formatDateTime = (isoString) => {
    if (!isoString) return 'Never';
    return new Date(isoString).toLocaleString();
  };

  return (
    <div className="dashboard">
      <div className="page-header">
        <h1 className="page-title">Dashboard</h1>
        <p className="page-subtitle">VVAULT home surface ready for cleanup.</p>
      </div>

      <div className="stats-grid">
        <div className="card">
          <div className="card-header">
            <h3 className="card-title">Capsules</h3>
          </div>
          <div className="stat-value">{stats.totalCapsules}</div>
          <div className="stat-label">Total capsules stored</div>
        </div>

        <div className="card">
          <div className="card-header">
            <h3 className="card-title">System Uptime</h3>
          </div>
          <div className="stat-value">{stats.systemUptime}</div>
          <div className="stat-label">Backend running time</div>
        </div>

        <div className="card">
          <div className="card-header">
            <h3 className="card-title">Backend Status</h3>
          </div>
          <div className={`status-indicator ${
            stats.backendStatus === 'running' ?
              'status-success' :
              stats.backendStatus === 'connecting' ?
                'status-warning' :
                'status-error'
          }`}>
            <span className="status-dot"></span>
            {stats.backendStatus}
          </div>
          <div className="stat-label">API server</div>
        </div>

        <div className="card">
          <div className="card-header">
            <h3 className="card-title">Last Sync</h3>
          </div>
          <div className="stat-value-small">{stats.lastSync}</div>
          <div className="stat-label">Server initialization</div>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
