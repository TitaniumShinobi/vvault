import React, { useState, useEffect } from 'react';

const Blockchain = () => {
  const [blockchainStatus, setBlockchainStatus] = useState({
    initialized: false,
    syncing: false,
    lastSync: null,
    blockHeight: 0,
    networkStatus: 'disconnected'
  });
  
  const [syncLog, setSyncLog] = useState([
    { time: new Date().toISOString(), message: 'Blockchain service ready', type: 'info' },
    { time: new Date().toISOString(), message: 'Waiting for initialization...', type: 'warning' }
  ]);
  
  useEffect(() => {
    // Simulate blockchain status updates
    const interval = setInterval(() => {
      // This would normally fetch real blockchain status
      checkBlockchainStatus();
    }, 10000);
    
    return () => clearInterval(interval);
  }, []);
  
  const checkBlockchainStatus = async () => {
    // Simulate checking blockchain status
    // In a real implementation, this would call the backend API
    try {
      // Placeholder for actual blockchain status check
      setBlockchainStatus(prev => ({
        ...prev,
        blockHeight: prev.blockHeight + Math.floor(Math.random() * 3),
        lastSync: new Date().toISOString()
      }));
    } catch (error) {
      console.error('Error checking blockchain status:', error);
    }
  };
  
  const initializeBlockchain = async () => {
    setBlockchainStatus(prev => ({ ...prev, syncing: true }));
    
    try {
      // Simulate blockchain initialization
      addLogEntry('Initializing blockchain connection...', 'info');
      
      setTimeout(() => {
        setBlockchainStatus(prev => ({
          ...prev,
          initialized: true,
          syncing: false,
          networkStatus: 'connected',
          lastSync: new Date().toISOString()
        }));
        addLogEntry('Blockchain initialized successfully', 'success');
      }, 2000);
      
    } catch (error) {
      setBlockchainStatus(prev => ({ ...prev, syncing: false }));
      addLogEntry(`Blockchain initialization failed: ${error.message}`, 'error');
    }
  };
  
  const syncCapsules = async () => {
    if (!blockchainStatus.initialized) {
      addLogEntry('Blockchain not initialized', 'error');
      return;
    }
    
    setBlockchainStatus(prev => ({ ...prev, syncing: true }));
    addLogEntry('Starting capsule sync to blockchain...', 'info');
    
    try {
      // Simulate capsule sync process
      setTimeout(() => {
        setBlockchainStatus(prev => ({
          ...prev,
          syncing: false,
          lastSync: new Date().toISOString()
        }));
        addLogEntry('Capsule sync completed successfully', 'success');
      }, 3000);
      
    } catch (error) {
      setBlockchainStatus(prev => ({ ...prev, syncing: false }));
      addLogEntry(`Capsule sync failed: ${error.message}`, 'error');
    }
  };
  
  const addLogEntry = (message, type = 'info') => {
    const entry = {
      time: new Date().toISOString(),
      message,
      type
    };
    setSyncLog(prev => [entry, ...prev.slice(0, 19)]); // Keep last 20 entries
  };
  
  const formatTime = (isoString) => {
    return new Date(isoString).toLocaleTimeString();
  };
  
  const getStatusColor = (status) => {
    switch (status) {
      case 'connected': return 'status-success';
      case 'connecting': return 'status-warning';
      case 'disconnected': return 'status-error';
      default: return 'status-info';
    }
  };
  
  const getLogTypeColor = (type) => {
    switch (type) {
      case 'success': return 'status-success';
      case 'warning': return 'status-warning';
      case 'error': return 'status-error';
      default: return 'status-info';
    }
  };
  
  return (
    <div className="blockchain">
      <div className="page-header">
        <h1 className="page-title">⛓️ Blockchain Integration</h1>
        <p className="page-subtitle">Immutable storage and verification for your capsules</p>
      </div>
      
      <div className="stats-grid">
        <div className="card">
          <div className="card-header">
            <h3 className="card-title">🔗 Network Status</h3>
          </div>
          <div className={`status-indicator ${getStatusColor(blockchainStatus.networkStatus)}`}>
            <span className="status-dot"></span>
            {blockchainStatus.networkStatus}
          </div>
          <div className="stat-label">Blockchain network connection</div>
        </div>
        
        <div className="card">
          <div className="card-header">
            <h3 className="card-title">📊 Block Height</h3>
          </div>
          <div className="stat-value">{blockchainStatus.blockHeight.toLocaleString()}</div>
          <div className="stat-label">Current blockchain height</div>
        </div>
        
        <div className="card">
          <div className="card-header">
            <h3 className="card-title">🔄 Last Sync</h3>
          </div>
          <div className="stat-value-small">
            {blockchainStatus.lastSync ? 
              formatTime(blockchainStatus.lastSync) : 
              'Never'
            }
          </div>
          <div className="stat-label">Last capsule synchronization</div>
        </div>
        
        <div className="card">
          <div className="card-header">
            <h3 className="card-title">⚙️ Service Status</h3>
          </div>
          <div className={`status-indicator ${
            blockchainStatus.initialized ? 'status-success' : 'status-warning'
          }`}>
            <span className="status-dot"></span>
            {blockchainStatus.initialized ? 'Initialized' : 'Not Initialized'}
          </div>
          <div className="stat-label">Blockchain service state</div>
        </div>
      </div>
      
      <div className="content-grid">
        <div className="main-panel">
          <div className="card">
            <div className="card-header">
              <h3 className="card-title">🚀 Blockchain Operations</h3>
            </div>
            <div className="blockchain-actions">
              <button 
                className="btn btn-primary"
                onClick={initializeBlockchain}
                disabled={blockchainStatus.syncing || blockchainStatus.initialized}
              >
                {blockchainStatus.syncing ? (
                  <>
                    <div className="spinner"></div>
                    Initializing...
                  </>
                ) : (
                  <>
                    <span>🔗</span>
                    Initialize Blockchain
                  </>
                )}
              </button>
              
              <button 
                className="btn btn-secondary"
                onClick={syncCapsules}
                disabled={!blockchainStatus.initialized || blockchainStatus.syncing}
              >
                {blockchainStatus.syncing ? (
                  <>
                    <div className="spinner"></div>
                    Syncing...
                  </>
                ) : (
                  <>
                    <span>📦</span>
                    Sync All Capsules
                  </>
                )}
              </button>
              
              <button 
                className="btn btn-secondary"
                disabled={!blockchainStatus.initialized}
              >
                <span>🔍</span>
                Verify Integrity
              </button>
              
              <button 
                className="btn btn-secondary"
                onClick={checkBlockchainStatus}
              >
                <span>🔄</span>
                Refresh Status
              </button>
            </div>
          </div>
          
          <div className="card mt-4">
            <div className="card-header">
              <h3 className="card-title">📋 Blockchain Information</h3>
            </div>
            <div className="blockchain-info">
              <div className="info-section">
                <h4>🔧 Configuration</h4>
                <div className="info-grid">
                  <div className="info-item">
                    <span className="info-label">Network:</span>
                    <span className="info-value">Ethereum (Local)</span>
                  </div>
                  <div className="info-item">
                    <span className="info-label">Contract Address:</span>
                    <span className="info-value">0x1234...5678</span>
                  </div>
                  <div className="info-item">
                    <span className="info-label">Gas Price:</span>
                    <span className="info-value">20 Gwei</span>
                  </div>
                  <div className="info-item">
                    <span className="info-label">Block Time:</span>
                    <span className="info-value">~15 seconds</span>
                  </div>
                </div>
              </div>
              
              <div className="info-section mt-4">
                <h4>📊 Statistics</h4>
                <div className="info-grid">
                  <div className="info-item">
                    <span className="info-label">Synced Capsules:</span>
                    <span className="info-value">0</span>
                  </div>
                  <div className="info-item">
                    <span className="info-label">Pending Transactions:</span>
                    <span className="info-value">0</span>
                  </div>
                  <div className="info-item">
                    <span className="info-label">Total Gas Used:</span>
                    <span className="info-value">0 ETH</span>
                  </div>
                  <div className="info-item">
                    <span className="info-label">Success Rate:</span>
                    <span className="info-value">100%</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
        
        <div className="side-panel">
          <div className="card">
            <div className="card-header">
              <h3 className="card-title">📈 Sync Log</h3>
            </div>
            <div className="sync-log">
              {syncLog.map((entry, index) => (
                <div key={index} className="log-entry">
                  <div className="log-time">{formatTime(entry.time)}</div>
                  <div className={`log-message ${getLogTypeColor(entry.type)}`}>
                    {entry.message}
                  </div>
                </div>
              ))}
            </div>
          </div>
          
          <div className="card mt-4">
            <div className="card-header">
              <h3 className="card-title">🔒 Security Features</h3>
            </div>
            <div className="security-features">
              <div className="feature-item">
                <span className="feature-icon">🔐</span>
                <div className="feature-info">
                  <div className="feature-name">Immutable Storage</div>
                  <div className="feature-desc">Capsules are stored immutably on blockchain</div>
                </div>
              </div>
              
              <div className="feature-item">
                <span className="feature-icon">🔍</span>
                <div className="feature-info">
                  <div className="feature-name">Integrity Verification</div>
                  <div className="feature-desc">Automatic hash verification for all capsules</div>
                </div>
              </div>
              
              <div className="feature-item">
                <span className="feature-icon">🌐</span>
                <div className="feature-info">
                  <div className="feature-name">Decentralized Backup</div>
                  <div className="feature-desc">Distributed storage across network nodes</div>
                </div>
              </div>
              
              <div className="feature-item">
                <span className="feature-icon">⏱️</span>
                <div className="feature-info">
                  <div className="feature-name">Timestamped Records</div>
                  <div className="feature-desc">Immutable creation and modification timestamps</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Blockchain;