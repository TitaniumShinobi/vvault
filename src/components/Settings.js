import React, { useState, useEffect, useCallback } from 'react';
import { authFetch } from '../utils/authFetch';
import {
  buildDefaultProfile,
  getSchema,
  toVvaultCapsulePayload
} from '../engine/orchestration/personalizationProfileService';

const Settings = ({ systemInfo, user }) => {
  const [settings, setSettings] = useState({
    autoSync: true,
    backupInterval: '24',
    maxCapsuleSize: '100',
    enableEncryption: true,
    blockchainNetwork: 'ethereum',
    debugLogging: false,
    apiTimeout: '30',
    corsOrigins: 'http://localhost:7784'
  });
  
  const [config, setConfig] = useState(null);
  const [saved, setSaved] = useState(false);
  const [schemaExported, setSchemaExported] = useState(false);
  const [capsuleExported, setCapsuleExported] = useState(false);
  
  useEffect(() => {
    loadConfig();
  }, []);
  
  const loadConfig = async () => {
    try {
      const response = await authFetch('/api/config');
      const data = await response.json();
      setConfig(data);
    } catch (error) {
      console.error('Failed to load config:', error);
    }
  };
  
  const handleSettingChange = (key, value) => {
    setSettings(prev => ({
      ...prev,
      [key]: value
    }));
  };
  
  const saveSettings = async () => {
    try {
      // In a real implementation, this would save to the backend
      console.log('Saving settings:', settings);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (error) {
      console.error('Failed to save settings:', error);
    }
  };
  
  const resetSettings = () => {
    setSettings({
      autoSync: true,
      backupInterval: '24',
      maxCapsuleSize: '100',
      enableEncryption: true,
      blockchainNetwork: 'ethereum',
      debugLogging: false,
      apiTimeout: '30',
      corsOrigins: 'http://localhost:7784'
    });
  };

  const downloadJson = (data, filename) => {
    const blob = new Blob([JSON.stringify(data, null, 2)], {
      type: 'application/json'
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const exportSchema = () => {
    const schema = getSchema();
    downloadJson(schema, 'chatty-personalization-schema.json');
    setSchemaExported(true);
    setTimeout(() => setSchemaExported(false), 3000);
  };

  const exportHumanCapsule = () => {
    const identity = {
      userId: config?.user_id || 'pending-chatty-user',
      email: config?.user_email || 'pending@vvault.local',
      vvaultUserId: config?.vvault_user_id || 'pending-vvault-user',
      linkedAccounts: {
        neatUserId: config?.neat_user_id || 'pending-neat-user',
        neatLinked: Boolean(config?.neat_user_id)
      }
    };

    const profile = buildDefaultProfile(identity);
    const payload = toVvaultCapsulePayload(profile);
    downloadJson({ profile, capsulePayload: payload }, 'vvault-human-capsule.json');
    setCapsuleExported(true);
    setTimeout(() => setCapsuleExported(false), 3000);
  };
  
  const exportConfig = () => {
    const configData = {
      settings,
      systemInfo,
      exportedAt: new Date().toISOString()
    };
    
    const blob = new Blob([JSON.stringify(configData, null, 2)], {
      type: 'application/json'
    });
    
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'vvault-config.json';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };
  
  return (
    <div className="settings">
      <div className="page-header">
        <h1 className="page-title">⚙️ Settings</h1>
        <p className="page-subtitle">Configure your VVAULT system preferences</p>
      </div>
      
      <div className="content-grid">
        <div className="main-panel">
          <div className="card">
            <div className="card-header">
              <h3 className="card-title">🔧 General Settings</h3>
            </div>
            <div className="settings-section">
              <div className="setting-group">
                <div className="setting-item">
                  <label className="setting-label">
                    <input
                      type="checkbox"
                      checked={settings.autoSync}
                      onChange={(e) => handleSettingChange('autoSync', e.target.checked)}
                    />
                    <span className="setting-title">Auto Sync to Blockchain</span>
                  </label>
                  <p className="setting-description">
                    Automatically sync new capsules to the blockchain
                  </p>
                </div>
                
                <div className="setting-item">
                  <label className="setting-label">
                    <span className="setting-title">Backup Interval (hours)</span>
                  </label>
                  <select
                    className="form-input"
                    value={settings.backupInterval}
                    onChange={(e) => handleSettingChange('backupInterval', e.target.value)}
                  >
                    <option value="1">1 hour</option>
                    <option value="6">6 hours</option>
                    <option value="12">12 hours</option>
                    <option value="24">24 hours</option>
                    <option value="168">Weekly</option>
                  </select>
                  <p className="setting-description">
                    How often to create automatic backups
                  </p>
                </div>
                
                <div className="setting-item">
                  <label className="setting-label">
                    <span className="setting-title">Max Capsule Size (MB)</span>
                  </label>
                  <input
                    type="number"
                    className="form-input"
                    value={settings.maxCapsuleSize}
                    onChange={(e) => handleSettingChange('maxCapsuleSize', e.target.value)}
                    min="1"
                    max="1000"
                  />
                  <p className="setting-description">
                    Maximum size limit for individual capsules
                  </p>
                </div>
              </div>
            </div>
          </div>
          
          <div className="card mt-4">
            <div className="card-header">
              <h3 className="card-title">🔒 Security Settings</h3>
            </div>
            <div className="settings-section">
              <div className="setting-group">
                <div className="setting-item">
                  <label className="setting-label">
                    <input
                      type="checkbox"
                      checked={settings.enableEncryption}
                      onChange={(e) => handleSettingChange('enableEncryption', e.target.checked)}
                    />
                    <span className="setting-title">Enable Encryption</span>
                  </label>
                  <p className="setting-description">
                    Encrypt capsule data before storage
                  </p>
                </div>
                
                <div className="setting-item">
                  <label className="setting-label">
                    <span className="setting-title">Blockchain Network</span>
                  </label>
                  <select
                    className="form-input"
                    value={settings.blockchainNetwork}
                    onChange={(e) => handleSettingChange('blockchainNetwork', e.target.value)}
                  >
                    <option value="ethereum">Ethereum</option>
                    <option value="polygon">Polygon</option>
                    <option value="binance">Binance Smart Chain</option>
                    <option value="local">Local Testnet</option>
                  </select>
                  <p className="setting-description">
                    Choose the blockchain network for storage
                  </p>
                </div>
              </div>
            </div>
          </div>
          
          <div className="card mt-4">
            <div className="card-header">
              <h3 className="card-title">🌐 Network Settings</h3>
            </div>
            <div className="settings-section">
              <div className="setting-group">
                <div className="setting-item">
                  <label className="setting-label">
                    <span className="setting-title">API Timeout (seconds)</span>
                  </label>
                  <input
                    type="number"
                    className="form-input"
                    value={settings.apiTimeout}
                    onChange={(e) => handleSettingChange('apiTimeout', e.target.value)}
                    min="5"
                    max="300"
                  />
                  <p className="setting-description">
                    Timeout for API requests to the backend
                  </p>
                </div>
                
                <div className="setting-item">
                  <label className="setting-label">
                    <span className="setting-title">CORS Origins</span>
                  </label>
                  <input
                    type="text"
                    className="form-input"
                    value={settings.corsOrigins}
                    onChange={(e) => handleSettingChange('corsOrigins', e.target.value)}
                    placeholder="http://localhost:7784"
                  />
                  <p className="setting-description">
                    Allowed origins for cross-origin requests
                  </p>
                </div>
              </div>
            </div>
          </div>
          
          <div className="card mt-4">
            <div className="card-header">
              <h3 className="card-title">🐛 Developer Settings</h3>
            </div>
            <div className="settings-section">
              <div className="setting-group">
                <div className="setting-item">
                  <label className="setting-label">
                    <input
                      type="checkbox"
                      checked={settings.debugLogging}
                      onChange={(e) => handleSettingChange('debugLogging', e.target.checked)}
                    />
                    <span className="setting-title">Enable Debug Logging</span>
                  </label>
                  <p className="setting-description">
                    Show detailed debug information in logs
                  </p>
                </div>
              </div>
            </div>
          </div>

          <div className="card mt-4">
            <div className="card-header">
              <h3 className="card-title">🧭 Identity & Personalization Capsule</h3>
            </div>
            <div className="settings-section">
              <div className="setting-group">
                <div className="setting-item">
                  <p className="setting-description">
                    Export the centralized Chatty personalization schema and a default
                    VVAULT-ready human capsule (links Chatty, VVAULT, and neat identities).
                  </p>
                  <div className="settings-actions">
                    <button className="btn btn-secondary" onClick={exportSchema}>
                      <span>📜</span>
                      {schemaExported ? 'Schema Exported' : 'Export Schema'}
                    </button>
                    <button className="btn btn-primary" onClick={exportHumanCapsule}>
                      <span>🧠</span>
                      {capsuleExported ? 'Capsule Exported' : 'Export Human Capsule'}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
          
          <div className="settings-actions">
            <button className="btn btn-primary" onClick={saveSettings}>
              {saved ? (
                <>
                  <span>✅</span>
                  Saved!
                </>
              ) : (
                <>
                  <span>💾</span>
                  Save Settings
                </>
              )}
            </button>
            <button className="btn btn-secondary" onClick={resetSettings}>
              <span>🔄</span>
              Reset to Defaults
            </button>
            <button className="btn btn-secondary" onClick={exportConfig}>
              <span>📤</span>
              Export Config
            </button>
          </div>
        </div>
        
        <div className="side-panel">
          <div className="card">
            <div className="card-header">
              <h3 className="card-title">📊 System Information</h3>
            </div>
            <div className="system-info">
              {config && (
                <>
                  <div className="info-section">
                    <h4>🌐 Server Configuration</h4>
                    <div className="info-item">
                      <span className="info-label">Backend Port:</span>
                      <span className="info-value">{config.backend_port}</span>
                    </div>
                    <div className="info-item">
                      <span className="info-label">Frontend Port:</span>
                      <span className="info-value">{config.frontend_port}</span>
                    </div>
                    <div className="info-item">
                      <span className="info-label">Project Directory:</span>
                      <span className="info-value">{config.project_dir}</span>
                    </div>
                    <div className="info-item">
                      <span className="info-label">Capsules Directory:</span>
                      <span className="info-value">{config.capsules_dir}</span>
                    </div>
                  </div>
                </>
              )}
              
              {systemInfo && (
                <div className="info-section mt-4">
                  <h4>⚡ Runtime Status</h4>
                  <div className="info-item">
                    <span className="info-label">Server Started:</span>
                    <span className="info-value">
                      {new Date(systemInfo.server_started).toLocaleString()}
                    </span>
                  </div>
                  <div className="info-item">
                    <span className="info-label">System Status:</span>
                    <span className={`status-indicator ${
                      systemInfo.system_status === 'running' ? 'status-success' : 'status-warning'
                    }`}>
                      <span className="status-dot"></span>
                      {systemInfo.system_status}
                    </span>
                  </div>
                  <div className="info-item">
                    <span className="info-label">Capsules Loaded:</span>
                    <span className="info-value">{systemInfo.capsules_loaded}</span>
                  </div>
                </div>
              )}
            </div>
          </div>
          
          <div className="card mt-4">
            <div className="card-header">
              <h3 className="card-title">💡 Quick Tips</h3>
            </div>
            <div className="tips-content">
              <div className="tip-item">
                <span className="tip-icon">🔒</span>
                <div className="tip-text">
                  <strong>Security:</strong> Always enable encryption for sensitive capsules
                </div>
              </div>
              
              <div className="tip-item">
                <span className="tip-icon">⛓️</span>
                <div className="tip-text">
                  <strong>Blockchain:</strong> Auto-sync ensures your data is always backed up
                </div>
              </div>
              
              <div className="tip-item">
                <span className="tip-icon">📦</span>
                <div className="tip-text">
                  <strong>Storage:</strong> Monitor capsule sizes to optimize performance
                </div>
              </div>
              
              <div className="tip-item">
                <span className="tip-icon">🔄</span>
                <div className="tip-text">
                  <strong>Backups:</strong> Regular backups prevent data loss
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Settings;