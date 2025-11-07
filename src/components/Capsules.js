import React, { useState, useEffect } from 'react';

const Capsules = () => {
  const [capsules, setCapsules] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedCapsule, setSelectedCapsule] = useState(null);
  const [error, setError] = useState(null);
  
  useEffect(() => {
    loadCapsules();
  }, []);
  
  const loadCapsules = async () => {
    try {
      setLoading(true);
      const response = await fetch('/api/capsules');
      const data = await response.json();
      
      if (data.success) {
        setCapsules(data.capsules);
      } else {
        setError(data.error);
      }
    } catch (err) {
      setError('Failed to load capsules');
      console.error('Error loading capsules:', err);
    } finally {
      setLoading(false);
    }
  };
  
  const formatFileSize = (bytes) => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };
  
  const formatDate = (isoString) => {
    return new Date(isoString).toLocaleDateString();
  };
  
  const viewCapsule = async (capsuleName) => {
    try {
      const response = await fetch(`/api/capsules/${capsuleName}`);
      const data = await response.json();
      
      if (data.success) {
        setSelectedCapsule({ name: capsuleName, data: data.capsule });
      } else {
        setError(data.error);
      }
    } catch (err) {
      setError('Failed to load capsule data');
      console.error('Error loading capsule:', err);
    }
  };
  
  if (loading) {
    return (
      <div className="loading-container">
        <div className="spinner"></div>
        <p>Loading capsules...</p>
      </div>
    );
  }
  
  return (
    <div className="capsules">
      <div className="page-header">
        <h1 className="page-title">📦 Capsules</h1>
        <p className="page-subtitle">Manage your AI construct memory capsules</p>
      </div>
      
      {error && (
        <div className="error-message">
          <span>❌ {error}</span>
          <button onClick={() => setError(null)}>×</button>
        </div>
      )}
      
      <div className="capsules-actions">
        <button className="btn btn-primary" onClick={loadCapsules}>
          <span>🔄</span>
          Refresh
        </button>
        <button className="btn btn-secondary">
          <span>➕</span>
          Create New
        </button>
        <button className="btn btn-secondary">
          <span>📤</span>
          Import
        </button>
      </div>
      
      <div className="content-grid">
        <div className="main-panel">
          <div className="card">
            <div className="card-header">
              <h3 className="card-title">
                Capsule Library ({capsules.length})
              </h3>
            </div>
            
            {capsules.length === 0 ? (
              <div className="empty-state">
                <div className="empty-icon">📦</div>
                <h3>No capsules found</h3>
                <p>Create your first capsule to get started</p>
                <button className="btn btn-primary">
                  <span>➕</span>
                  Create Capsule
                </button>
              </div>
            ) : (
              <div className="capsules-grid">
                {capsules.map((capsule, index) => (
                  <div key={index} className="capsule-card">
                    <div className="capsule-header">
                      <div className="capsule-icon">📦</div>
                      <div className="capsule-info">
                        <h4 className="capsule-name">{capsule.title || capsule.name}</h4>
                        <p className="capsule-description">
                          {capsule.description || 'No description available'}
                        </p>
                      </div>
                    </div>
                    
                    <div className="capsule-meta">
                      <span className="capsule-size">{formatFileSize(capsule.size)}</span>
                      <span className="capsule-date">{formatDate(capsule.modified)}</span>
                      <span className="capsule-version">v{capsule.version || '1.0.0'}</span>
                    </div>
                    
                    {capsule.tags && capsule.tags.length > 0 && (
                      <div className="capsule-tags">
                        {capsule.tags.slice(0, 3).map((tag, tagIndex) => (
                          <span key={tagIndex} className="capsule-tag">{tag}</span>
                        ))}
                        {capsule.tags.length > 3 && (
                          <span className="capsule-tag">+{capsule.tags.length - 3}</span>
                        )}
                      </div>
                    )}
                    
                    <div className="capsule-actions">
                      <button 
                        className="btn btn-secondary btn-sm"
                        onClick={() => viewCapsule(capsule.name)}
                      >
                        <span>👁️</span>
                        View
                      </button>
                      <button className="btn btn-secondary btn-sm">
                        <span>📤</span>
                        Export
                      </button>
                      <button className="btn btn-secondary btn-sm">
                        <span>🔍</span>
                        Verify
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
        
        <div className="side-panel">
          {selectedCapsule ? (
            <div className="card">
              <div className="card-header">
                <h3 className="card-title">📖 Capsule Details</h3>
                <button 
                  className="btn btn-secondary btn-sm"
                  onClick={() => setSelectedCapsule(null)}
                >
                  ×
                </button>
              </div>
              <div className="capsule-details">
                <h4>{selectedCapsule.name}</h4>
                <pre className="capsule-json">
                  {JSON.stringify(selectedCapsule.data, null, 2)}
                </pre>
              </div>
            </div>
          ) : (
            <div className="card">
              <div className="card-header">
                <h3 className="card-title">ℹ️ Information</h3>
              </div>
              <div className="info-content">
                <p>Select a capsule to view its details.</p>
                <br />
                <h4>Capsule Types:</h4>
                <ul>
                  <li><strong>Memory:</strong> AI conversation history</li>
                  <li><strong>Data:</strong> Structured information</li>
                  <li><strong>Config:</strong> System configurations</li>
                  <li><strong>Backup:</strong> Archived snapshots</li>
                </ul>
              </div>
            </div>
          )}
          
          <div className="card mt-4">
            <div className="card-header">
              <h3 className="card-title">📊 Statistics</h3>
            </div>
            <div className="stats-content">
              <div className="stat-row">
                <span className="stat-label">Total Capsules:</span>
                <span className="stat-value">{capsules.length}</span>
              </div>
              <div className="stat-row">
                <span className="stat-label">Total Size:</span>
                <span className="stat-value">
                  {formatFileSize(capsules.reduce((sum, c) => sum + c.size, 0))}
                </span>
              </div>
              <div className="stat-row">
                <span className="stat-label">Latest:</span>
                <span className="stat-value">
                  {capsules.length > 0 ? 
                    formatDate(capsules.sort((a, b) => 
                      new Date(b.modified) - new Date(a.modified)
                    )[0].modified) : 'None'
                  }
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Capsules;