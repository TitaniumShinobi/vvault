import React, { useState, useEffect, useCallback } from 'react';
import { authFetch } from '../utils/authFetch';
import './VaultBrowser.css';

const CONSTRUCT_COLORS = {
  'nova': '#9b59b6',
  'zen': '#3498db',
  'katana': '#e74c3c',
  'lin': '#2ecc71',
  'default': '#95a5a6'
};

const getConstructColor = (constructId) => {
  const name = constructId.toLowerCase().replace(/-\d+$/, '');
  return CONSTRUCT_COLORS[name] || CONSTRUCT_COLORS.default;
};

const getLogicalPath = (file) => {
  let path = file.display_path || file.storage_path || file.filename || '';
  
  path = path.replace(/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\//, '');
  
  path = path.replace(/^[a-z_]+_\d+\//, '');
  
  return path || file.filename || 'unknown';
};

const VaultBrowser = ({ user }) => {
  const [files, setFiles] = useState([]);
  const [currentPath, setCurrentPath] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedFile, setSelectedFile] = useState(null);
  const [fileContent, setFileContent] = useState(null);
  const [viewMode, setViewMode] = useState('list');
  const [constructs, setConstructs] = useState([]);
  const [userInfo, setUserInfo] = useState({ root_label: 'Vault', is_admin: false });

  const fetchConstructs = useCallback(async () => {
    try {
      const response = await authFetch('/api/chatty/constructs');
      const data = await response.json();
      if (data.success && data.constructs) {
        const formatted = data.constructs.map(c => ({
          id: c.construct_id,
          name: c.construct_id.replace(/-\d+$/, '').charAt(0).toUpperCase() + 
                c.construct_id.replace(/-\d+$/, '').slice(1),
          color: getConstructColor(c.construct_id)
        }));
        setConstructs(formatted);
      }
    } catch (err) {
      console.error('Failed to fetch constructs:', err);
    }
  }, []);

  const fetchUserInfo = useCallback(async () => {
    try {
      const response = await authFetch('/api/vault/user-info');
      const data = await response.json();
      if (data.success) {
        setUserInfo({
          root_label: data.root_label || 'Vault',
          display_name: data.display_name,
          is_admin: data.is_admin || false
        });
      }
    } catch (err) {
      console.error('Failed to fetch user info:', err);
    }
  }, []);

  const fetchFiles = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await authFetch('/api/vault/files');
      const data = await response.json();
      if (data.success) {
        setFiles(data.files || []);
        if (data.user_root) {
          setUserInfo(prev => ({ ...prev, root_label: data.user_root }));
        }
      } else {
        setError(data.error || 'Failed to load files');
      }
    } catch (err) {
      setError('Failed to connect to server');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchUserInfo();
    fetchFiles();
    fetchConstructs();
  }, [fetchUserInfo, fetchFiles, fetchConstructs]);

  const buildHierarchy = (files) => {
    const hierarchy = { folders: {}, files: [] };
    
    files.forEach(file => {
      const logicalPath = getLogicalPath(file);
      const parts = logicalPath.split('/').filter(p => p);
      
      if (parts.length === 0) {
        hierarchy.files.push({ ...file, displayName: file.filename });
      } else if (parts.length === 1) {
        hierarchy.files.push({ ...file, displayName: parts[0] });
      } else {
        let current = hierarchy;
        for (let i = 0; i < parts.length - 1; i++) {
          const folderName = parts[i];
          if (!current.folders[folderName]) {
            current.folders[folderName] = { folders: {}, files: [] };
          }
          current = current.folders[folderName];
        }
        current.files.push({ ...file, displayName: parts[parts.length - 1] });
      }
    });
    
    return hierarchy;
  };

  const getCurrentFolder = () => {
    const hierarchy = buildHierarchy(files);
    let current = hierarchy;
    
    for (const folder of currentPath) {
      if (current.folders[folder]) {
        current = current.folders[folder];
      } else {
        return { folders: {}, files: [] };
      }
    }
    
    return current;
  };

  const navigateToFolder = (folderName) => {
    setCurrentPath([...currentPath, folderName]);
    setSelectedFile(null);
    setFileContent(null);
  };

  const navigateBack = () => {
    setCurrentPath(currentPath.slice(0, -1));
    setSelectedFile(null);
    setFileContent(null);
  };

  const navigateHome = () => {
    setCurrentPath([]);
    setSelectedFile(null);
    setFileContent(null);
  };

  const navigateToBreadcrumb = (index) => {
    setCurrentPath(currentPath.slice(0, index + 1));
    setSelectedFile(null);
    setFileContent(null);
  };

  const selectFile = async (file) => {
    setSelectedFile(file);
    const textTypes = ['text', 'text/plain', 'text/markdown', 'conversation', 'transcript', 'prompt', 'config', 'identity'];
    const isTextLike = !file.file_type || textTypes.includes(file.file_type) || file.file_type.startsWith('text/');
    if (isTextLike) {
      if (file.content) {
        setFileContent(file.content);
      } else {
        try {
          const response = await authFetch(`/api/vault/files/${file.id}`);
          const data = await response.json();
          if (data.success && data.file && data.file.content) {
            setFileContent(data.file.content);
          } else {
            setFileContent(null);
          }
        } catch (err) {
          console.error('Failed to fetch file content:', err);
          setFileContent(null);
        }
      }
    } else {
      setFileContent(null);
    }
  };

  const getFileIcon = (filename, isFolder = false, fileType = 'text') => {
    if (isFolder) return '📁';
    if (fileType === 'binary') {
      const ext = filename.split('.').pop()?.toLowerCase();
      const icons = {
        pdf: '📄', png: '🖼️', jpg: '🖼️', jpeg: '🖼️', gif: '🖼️', webp: '🖼️',
        mp4: '🎬', mp3: '🎵', wav: '🎵', mov: '🎬',
        doc: '📝', docx: '📝', xls: '📊', xlsx: '📊', ppt: '📽️', pptx: '📽️',
        zip: '📦', tar: '📦', gz: '📦'
      };
      return icons[ext] || '📎';
    }
    const ext = filename.split('.').pop()?.toLowerCase();
    const icons = {
      md: '📝', json: '📋', txt: '📄', yaml: '⚙️', yml: '⚙️',
      py: '🐍', js: '💛', ts: '💙', jsx: '⚛️', tsx: '⚛️',
      css: '🎨', html: '🌐', sql: '🗃️', sh: '⚡'
    };
    return icons[ext] || '📄';
  };

  const formatDate = (dateStr) => {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    return date.toLocaleDateString('en-US', { 
      month: 'short', day: 'numeric', year: 'numeric' 
    });
  };

  const formatSize = (bytes) => {
    if (!bytes) return '-';
    const kb = bytes / 1024;
    if (kb < 1024) return `${kb.toFixed(1)} KB`;
    return `${(kb / 1024).toFixed(1)} MB`;
  };

  const currentFolder = getCurrentFolder();
  const folderNames = Object.keys(currentFolder.folders).sort();
  const fileList = currentFolder.files.sort((a, b) => 
    (a.displayName || a.filename).localeCompare(b.displayName || b.filename)
  );

  const favorites = [
    { name: 'All Files', icon: '📂', path: [] },
    { name: 'Chats', icon: '💬', path: ['chats'] },
    { name: 'Prompts', icon: '✨', path: ['prompts'] },
    { name: 'Personality', icon: '🧠', path: ['personality'] },
    { name: 'Memory', icon: '💾', path: ['memory'] },
    { name: 'Documents', icon: '📚', path: ['documents'] }
  ];

  if (loading) {
    return (
      <div className="vault-browser">
        <div className="vault-loading">
          <div className="loading-spinner"></div>
          <p>Loading vault...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="vault-browser">
        <div className="vault-error">
          <span className="error-icon">⚠️</span>
          <p>{error}</p>
          <button onClick={fetchFiles}>Retry</button>
        </div>
      </div>
    );
  }

  return (
    <div className="vault-browser">
      <div className="vault-sidebar">
        <div className="sidebar-section">
          <h3>FAVORITES</h3>
          {favorites.map((fav, idx) => (
            <div 
              key={idx}
              className={`sidebar-item ${JSON.stringify(currentPath) === JSON.stringify(fav.path) ? 'active' : ''}`}
              onClick={() => setCurrentPath(fav.path)}
            >
              <span className="sidebar-icon">{fav.icon}</span>
              <span className="sidebar-label">{fav.name}</span>
            </div>
          ))}
        </div>
        
        <div className="sidebar-section">
          <h3>CONSTRUCTS</h3>
          {constructs.map((construct, idx) => (
            <div key={idx} className="sidebar-item">
              <span 
                className="construct-dot" 
                style={{ backgroundColor: construct.color }}
              ></span>
              <span className="sidebar-label">{construct.name}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="vault-main">
        <div className="vault-toolbar">
          <div className="toolbar-nav">
            <button 
              className="nav-button"
              onClick={navigateBack}
              disabled={currentPath.length === 0}
            >
              ←
            </button>
            <button 
              className="nav-button"
              onClick={navigateHome}
              disabled={currentPath.length === 0}
            >
              🏠
            </button>
          </div>
          
          <div className="breadcrumb">
            <span className="breadcrumb-icon">{userInfo.is_admin ? '🔐' : '🔒'}</span>
            <span 
              className="breadcrumb-item clickable"
              onClick={navigateHome}
            >
              {userInfo.root_label}
            </span>
            {currentPath.map((folder, idx) => (
              <React.Fragment key={idx}>
                <span className="breadcrumb-separator">/</span>
                <span 
                  className="breadcrumb-item clickable"
                  onClick={() => navigateToBreadcrumb(idx)}
                >
                  {folder}
                </span>
              </React.Fragment>
            ))}
          </div>

          <div className="toolbar-actions">
            <input 
              type="text" 
              placeholder="Search files..." 
              className="search-input"
            />
            <div className="view-toggle">
              <button 
                className={viewMode === 'list' ? 'active' : ''}
                onClick={() => setViewMode('list')}
              >
                ☰
              </button>
              <button 
                className={viewMode === 'grid' ? 'active' : ''}
                onClick={() => setViewMode('grid')}
              >
                ⊞
              </button>
            </div>
          </div>
        </div>

        <div className={`vault-content ${viewMode}`}>
          <div className="file-list">
            <div className="file-list-header">
              <span className="col-name">NAME</span>
              <span className="col-construct">CONSTRUCT</span>
              <span className="col-date">DATE MODIFIED</span>
              <span className="col-size">SIZE</span>
            </div>
            
            {folderNames.map((folderName, idx) => (
              <div 
                key={`folder-${idx}`}
                className="file-row folder"
                onDoubleClick={() => navigateToFolder(folderName)}
              >
                <span className="col-name">
                  <span className="file-icon">{getFileIcon(folderName, true)}</span>
                  <span className="file-name">{folderName}</span>
                </span>
                <span className="col-construct">-</span>
                <span className="col-date">-</span>
                <span className="col-size">-</span>
              </div>
            ))}
            
            {fileList.map((file, idx) => {
              const metadata = typeof file.metadata === 'string'
                ? JSON.parse(file.metadata)
                : file.metadata || {};
              
              return (
                <div 
                  key={`file-${idx}`}
                  className={`file-row ${selectedFile?.id === file.id ? 'selected' : ''}`}
                  onClick={() => selectFile(file)}
                >
                  <span className="col-name">
                    <span className="file-icon">
                      {getFileIcon(file.displayName || file.filename, false, file.file_type)}
                    </span>
                    <span className="file-name">{file.displayName || file.filename}</span>
                  </span>
                  <span className="col-construct">
                    {file.construct_id || '-'}
                  </span>
                  <span className="col-date">
                    {formatDate(file.created_at || metadata.migrated_at)}
                  </span>
                  <span className="col-size">
                    {formatSize(metadata.size)}
                  </span>
                </div>
              );
            })}
            
            {folderNames.length === 0 && fileList.length === 0 && (
              <div className="empty-folder">
                <span className="empty-icon">📭</span>
                <p>This folder is empty</p>
              </div>
            )}
          </div>
        </div>

        {selectedFile && (
          <div className="file-preview">
            <div className="preview-header">
              <h3>{selectedFile.displayName || selectedFile.filename}</h3>
              <button onClick={() => setSelectedFile(null)}>×</button>
            </div>
            <div className="preview-content">
              {selectedFile.file_type === 'binary' ? (
                <div className="binary-preview">
                  <span className="binary-icon">📎</span>
                  <p>Binary file - {selectedFile.filename}</p>
                  <p className="binary-info">Stored in cloud storage</p>
                </div>
              ) : (
                <pre>{fileContent || 'No content available'}</pre>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default VaultBrowser;
