import React, { useState, useEffect, useCallback } from 'react';
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

const VaultBrowser = ({ user }) => {
  const [files, setFiles] = useState([]);
  const [currentPath, setCurrentPath] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedFile, setSelectedFile] = useState(null);
  const [fileContent, setFileContent] = useState(null);
  const [viewMode, setViewMode] = useState('list');
  const [constructs, setConstructs] = useState([]);

  const getAuthHeaders = useCallback(() => {
    const token = user?.token || localStorage.getItem('vvault_token');
    return token ? { 'Authorization': `Bearer ${token}` } : {};
  }, [user]);

  const fetchConstructs = useCallback(async () => {
    try {
      const response = await fetch('/api/chatty/constructs', {
        headers: getAuthHeaders()
      });
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
  }, [getAuthHeaders]);

  const fetchFiles = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch('/api/vault/files', {
        headers: getAuthHeaders()
      });
      const data = await response.json();
      if (data.success) {
        setFiles(data.files || []);
      } else {
        setError(data.error || 'Failed to load files');
      }
    } catch (err) {
      setError('Failed to connect to server');
    } finally {
      setLoading(false);
    }
  }, [getAuthHeaders]);

  useEffect(() => {
    fetchFiles();
    fetchConstructs();
  }, [fetchFiles, fetchConstructs]);

  const buildHierarchy = (files) => {
    const hierarchy = { folders: {}, files: [] };
    
    files.forEach(file => {
      const metadata = typeof file.metadata === 'string' 
        ? JSON.parse(file.metadata) 
        : file.metadata || {};
      
      const originalPath = metadata.original_path || file.filename;
      const parts = originalPath.split('/').filter(p => p);
      
      if (parts.length === 1) {
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
    if (file.file_type === 'text' || !file.file_type) {
      setFileContent(file.content);
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
            <span className="breadcrumb-icon">🔒</span>
            <span 
              className="breadcrumb-item clickable"
              onClick={navigateHome}
            >
              Vault
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
