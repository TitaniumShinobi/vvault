import React, { useState, useEffect } from 'react';

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
        <h1 className="page-title">🏠 Dashboard</h1>
        <p className="page-subtitle">VVAULT system overview and status</p>
      </div>
      
      <div className="stats-grid">
        <div className="card">
          <div className="card-header">
            <h3 className="card-title">📦 Capsules</h3>
          </div>
          <div className="stat-value">{stats.totalCapsules}</div>
          <div className="stat-label">Total capsules stored</div>
        </div>
        
        <div className="card">
          <div className="card-header">
            <h3 className="card-title">⏱️ System Uptime</h3>
          </div>
          <div className="stat-value">{stats.systemUptime}</div>
          <div className="stat-label">Backend running time</div>
        </div>
        
        <div className="card">
          <div className="card-header">
            <h3 className="card-title">🔗 Backend Status</h3>
          </div>
          <div className={`status-indicator ${
            stats.backendStatus === 'running' ? 'status-success' : 
            stats.backendStatus === 'connecting' ? 'status-warning' : 'status-error'
          }`}>
            <span className="status-dot"></span>
            {stats.backendStatus}
          </div>
          <div className="stat-label">API server on port 8000</div>
        </div>
        
        <div className="card">
          <div className="card-header">
            <h3 className="card-title">🔄 Last Sync</h3>
          </div>
          <div className="stat-value-small">{stats.lastSync}</div>
          <div className="stat-label">Server initialization</div>
        </div>
      </div>
      
      <div className="content-grid">
        <div className="main-panel">
          <div className="card">
            <div className="card-header">
              <h3 className="card-title">🚀 Quick Actions</h3>
            </div>
            <div className="quick-actions">
              <button className="btn btn-primary">
                <span>📦</span>
                View All Capsules
              </button>
              <button className="btn btn-secondary">
                <span>➕</span>
                Create New Capsule
              </button>
              <button className="btn btn-secondary">
                <span>⛓️</span>
                Sync Blockchain
              </button>
              <button className="btn btn-secondary">
                <span>🔍</span>
                System Diagnostics
              </button>
            </div>
          </div>
          
          <div className="card mt-4">
            <div className="card-header">
              <h3 className="card-title">📊 System Information</h3>
            </div>
            <div className="system-info">
              <div className="info-row">
                <span className="info-label">Backend Port:</span>
                <span className="info-value">8000</span>
              </div>
              <div className="info-row">
                <span className="info-label">Frontend Port:</span>
                <span className="info-value">7784</span>
              </div>
              <div className="info-row">
                <span className="info-label">Version:</span>
                <span className="info-value">1.0.0</span>
              </div>
              <div className="info-row">
                <span className="info-label">Environment:</span>
                <span className="info-value">Development</span>
              </div>
            </div>
          </div>
        </div>
        
        <div className="side-panel">
          <div className="card">
            <div className="card-header">
              <h3 className="card-title">📈 Activity Log</h3>
            </div>
            <div className="activity-log">
              <div className="activity-item">
                <div className="activity-time">Just now</div>
                <div className="activity-message">Frontend connected to backend</div>
              </div>
              <div className="activity-item">
                <div className="activity-time">1 min ago</div>
                <div className="activity-message">VVAULT backend server started</div>
              </div>
              <div className="activity-item">
                <div className="activity-time">2 min ago</div>
                <div className="activity-message">System initialization completed</div>
              </div>
            </div>
          </div>
          
          <div className="card mt-4">
            <div className="card-header">
              <h3 className="card-title">🔒 Security Status</h3>
            </div>
            <div className="security-status">
              <div className="security-item">
                <span className="status-indicator status-success">
                  <span className="status-dot"></span>
                  Encrypted Storage
                </span>
              </div>
              <div className="security-item">
                <span className="status-indicator status-success">
                  <span className="status-dot"></span>
                  Secure Communications
                </span>
              </div>
              <div className="security-item">
                <span className="status-indicator status-warning">
                  <span className="status-dot"></span>
                  Blockchain Sync
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;