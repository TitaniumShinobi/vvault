import React, { useState, useEffect, useCallback } from 'react';
import { generateManifest } from 'material-icon-theme';
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

const materialIconManifest = generateManifest({ activeIconPack: 'react' });
const materialIconContext = require.context('material-icon-theme/icons', false, /\.svg$/);
const materialIconUrlsByFileName = materialIconContext.keys().reduce((acc, key) => {
  const fileName = key.split('/').pop();
  if (fileName) acc[fileName] = materialIconContext(key);
  return acc;
}, {});
const materialDefaultFileIcon = materialIconManifest.file || 'file';
const materialDefaultFolderIcon = materialIconManifest.folder || 'folder';
const materialFileExtensionKeys = Object.keys(materialIconManifest.fileExtensions || {})
  .sort((left, right) => right.length - left.length);
const materialFileNameIconOverrides = {
  'package-lock.json': 'npm',
  'package.json': 'npm',
  'readme.md': 'markdown'
};

const materialIconUrlForIconId = (iconId) => {
  const definition = materialIconManifest.iconDefinitions?.[iconId]
    || materialIconManifest.iconDefinitions?.[materialDefaultFileIcon];
  const iconFile = definition?.iconPath?.split('/').pop() || 'file.svg';
  return materialIconUrlsByFileName[iconFile] || materialIconUrlsByFileName['file.svg'] || '';
};

const getBaseName = (path = '') => {
  const normalized = String(path).replace(/\\/g, '/');
  return normalized.split('/').filter(Boolean).pop() || normalized;
};

const getExtension = (path = '') => {
  const basename = getBaseName(path).toLowerCase();
  const match = /\.([^.]+)$/.exec(basename);
  return match ? match[1] : '';
};

const materialIconIdForFile = (path = '') => {
  const lowerPath = String(path).toLowerCase();
  const basename = getBaseName(lowerPath);
  const byName = materialFileNameIconOverrides[basename]
    || materialIconManifest.fileNames?.[basename]
    || materialIconManifest.fileNames?.[lowerPath];
  if (byName) return byName;

  const extensionKey = materialFileExtensionKeys.find((key) => basename === key || basename.endsWith(`.${key}`));
  if (extensionKey) return materialIconManifest.fileExtensions?.[extensionKey] || materialDefaultFileIcon;

  return materialDefaultFileIcon;
};

const materialIconIdForFolder = (folderName = '') => {
  const normalized = getBaseName(folderName).toLowerCase();
  return materialIconManifest.folderNames?.[normalized] || materialDefaultFolderIcon;
};

const MaterialVaultIcon = ({ filename, isFolder = false }) => {
  const iconId = isFolder ? materialIconIdForFolder(filename) : materialIconIdForFile(filename);
  const iconUrl = materialIconUrlForIconId(iconId);
  return (
    <span
      className="material-file-icon"
      data-material-icon={iconId}
      aria-hidden="true"
    >
      {iconUrl ? <img src={iconUrl} alt="" draggable={false} /> : null}
    </span>
  );
};

const getLogicalPath = (file) => {
  let path = file.display_path || file.storage_path || '';
  
  path = path.replace(/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\//, '');
  path = path.replace(/^[a-z_]+_\d+\//, '');
  
  const filename = file.filename || 'unknown';
  
  if (path && path.includes('/')) {
    return path;
  }
  
  if (filename.includes('/')) {
    return filename;
  }
  
  let meta = file.metadata || {};
  if (typeof meta === 'string') {
    try { meta = JSON.parse(meta); } catch(e) { meta = {}; }
  }
  const folder = meta.folder || '';
  const constructId = file.construct_id || meta.construct_id || '';
  const metaType = meta.type || '';
  
  if (constructId && folder) {
    return `instances/${constructId}/${folder}/${filename}`;
  } else if (constructId) {
    return `instances/${constructId}/${filename}`;
  } else if (metaType === 'user_glyph') {
    return `account/${filename}`;
  }
  
  return path || filename;
};

const getFileExtension = (filename = '') => {
  const cleanName = filename.split('?')[0].split('#')[0];
  const parts = cleanName.split('.');
  return parts.length > 1 ? parts.pop().toLowerCase() : '';
};

const IMAGE_PREVIEW_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg']);

const getPreviewKind = (file = {}) => {
  const filename = file.displayName || file.filename || '';
  const ext = getFileExtension(filename);
  const fileType = (file.file_type || '').toLowerCase();

  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext) || fileType.startsWith('image/')) return 'image';
  if (ext === 'pdf' || fileType === 'application/pdf') return 'pdf';
  if (['mp3', 'wav', 'ogg', 'm4a'].includes(ext) || fileType.startsWith('audio/')) return 'audio';
  if (['mp4', 'webm', 'mov'].includes(ext) || fileType.startsWith('video/')) return 'video';
  if (ext === 'json' || fileType.includes('json') || ext === 'capsule') return 'json';
  if (ext === 'csv' || fileType.includes('csv')) return 'csv';
  if (['md', 'markdown'].includes(ext) || fileType.includes('markdown')) return 'markdown';
  if (['py', 'js', 'ts', 'jsx', 'tsx', 'css', 'html', 'sql', 'sh', 'yaml', 'yml'].includes(ext)) return 'code';
  if (['txt', 'log'].includes(ext) || fileType.startsWith('text/') || ['text', 'conversation', 'transcript', 'prompt', 'config', 'identity', 'ledger', 'simdrive'].includes(fileType)) return 'text';
  if (['doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx'].includes(ext)) return 'office';
  return 'metadata';
};

const canPreviewImage = (file = {}) => {
  const filename = file.displayName || file.filename || '';
  const ext = getFileExtension(filename);
  const fileType = (file.file_type || '').toLowerCase();
  return IMAGE_PREVIEW_EXTENSIONS.has(ext) || fileType.startsWith('image/');
};

const parseMetadata = (metadata) => {
  if (!metadata) return {};
  if (typeof metadata === 'string') {
    try { return JSON.parse(metadata); } catch (e) { return {}; }
  }
  return typeof metadata === 'object' ? metadata : {};
};

const getContentText = (content) => {
  if (content === null || content === undefined) return '';
  if (typeof content === 'string') return content;
  try {
    return JSON.stringify(content, null, 2);
  } catch (e) {
    return String(content);
  }
};

const looksLikeBase64 = (value) => {
  const text = (value || '').trim();
  return text.length > 32 && /^[A-Za-z0-9+/=\s]+$/.test(text);
};

const toDataUrl = (content, file) => {
  const text = getContentText(content).trim();
  if (!text) return '';
  if (text.startsWith('data:')) return text;
  if (!looksLikeBase64(text)) return '';

  const ext = getFileExtension(file.displayName || file.filename || '');
  const fileType = file.file_type && file.file_type !== 'binary' ? file.file_type : '';
  const mimeByExt = {
    png: 'image/png',
    jpg: 'image/jpeg',
    jpeg: 'image/jpeg',
    gif: 'image/gif',
    webp: 'image/webp',
    svg: 'image/svg+xml',
    pdf: 'application/pdf',
    mp3: 'audio/mpeg',
    wav: 'audio/wav',
    ogg: 'audio/ogg',
    m4a: 'audio/mp4',
    mp4: 'video/mp4',
    webm: 'video/webm',
    mov: 'video/quicktime'
  };
  const mime = fileType || mimeByExt[ext] || 'application/octet-stream';
  return `data:${mime};base64,${text.replace(/\s+/g, '')}`;
};

const formatJsonPreview = (content) => {
  const text = getContentText(content);
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch (e) {
    return text;
  }
};

const parseCsvPreview = (content) => {
  const rows = getContentText(content)
    .split(/\r?\n/)
    .filter(Boolean)
    .slice(0, 51)
    .map(row => row.split(',').map(cell => cell.trim()));
  if (rows.length === 0) return { headers: [], rows: [] };
  return { headers: rows[0], rows: rows.slice(1) };
};

const VaultBrowser = ({ user }) => {
  const [files, setFiles] = useState([]);
  const [currentPath, setCurrentPath] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedFile, setSelectedFile] = useState(null);
  const [fileContent, setFileContent] = useState(null);
  const [imagePreviewUrl, setImagePreviewUrl] = useState('');
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState(null);
  const [viewMode, setViewMode] = useState('list');
  const [constructs, setConstructs] = useState([]);
  const [userInfo, setUserInfo] = useState({ root_label: 'Vault', is_admin: false });
  const [syncingConstruct, setSyncingConstruct] = useState(null);
  const [syncResult, setSyncResult] = useState(null);
  const [uploadState, setUploadState] = useState({ active: false, progress: '', result: null });
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = React.useRef(null);

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

  const triggerMemupSync = async (constructId) => {
    setSyncingConstruct(constructId);
    setSyncResult(null);
    try {
      const response = await authFetch('/api/vault/memup/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ construct_id: constructId }),
      });
      const data = await response.json();
      setSyncResult(data);
      if (data.success) {
        fetchFiles();
      }
    } catch (err) {
      setSyncResult({ success: false, error: 'Sync request failed' });
    } finally {
      setSyncingConstruct(null);
    }
  };

  const getActiveConstructId = () => {
    if (currentPath.length >= 2 && currentPath[0] === 'instances') {
      return currentPath[1];
    }
    return null;
  };

  const handleUploadFiles = async (fileList) => {
    const constructId = getActiveConstructId();
    if (!constructId) {
      setUploadState({ active: false, progress: '', result: { success: false, error: 'Navigate to a construct folder first' } });
      return;
    }
    if (!fileList || fileList.length === 0) return;

    setUploadState({ active: true, progress: 'Preparing upload...', result: null });

    const formData = new FormData();
    formData.append('construct_id', constructId);
    for (let i = 0; i < fileList.length; i++) {
      formData.append('files', fileList[i]);
    }

    const totalSize = Array.from(fileList).reduce((s, f) => s + f.size, 0);
    const sizeMB = (totalSize / (1024 * 1024)).toFixed(1);
    setUploadState({ active: true, progress: `Uploading ${fileList.length} file(s) (${sizeMB} MB)...`, result: null });

    try {
      const response = await authFetch('/api/vault/knowledge-files/upload', {
        method: 'POST',
        body: formData,
      });
      const data = await response.json();
      setUploadState({ active: false, progress: '', result: data });
      if (data.success) {
        fetchFiles();
      }
      setTimeout(() => setUploadState(prev => ({ ...prev, result: null })), 8000);
    } catch (err) {
      setUploadState({ active: false, progress: '', result: { success: false, error: 'Upload failed: ' + err.message } });
      setTimeout(() => setUploadState(prev => ({ ...prev, result: null })), 8000);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      handleUploadFiles(e.dataTransfer.files);
    }
  };

  const handleDragOver = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(true);
  };

  const handleDragLeave = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
  };

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
    setImagePreviewUrl('');
    setPreviewError(null);
  };

  const navigateBack = () => {
    setCurrentPath(currentPath.slice(0, -1));
    setSelectedFile(null);
    setFileContent(null);
    setImagePreviewUrl('');
    setPreviewError(null);
  };

  const navigateHome = () => {
    setCurrentPath([]);
    setSelectedFile(null);
    setFileContent(null);
    setImagePreviewUrl('');
    setPreviewError(null);
  };

  const navigateToBreadcrumb = (index) => {
    setCurrentPath(currentPath.slice(0, index + 1));
    setSelectedFile(null);
    setFileContent(null);
    setImagePreviewUrl('');
    setPreviewError(null);
  };

  const selectFile = async (file) => {
    setSelectedFile(file);
    setFileContent(file.content ?? null);
    setImagePreviewUrl('');
    setPreviewError(null);
    setPreviewLoading(true);

    try {
      const isImage = canPreviewImage(file);
      const response = await authFetch(isImage ? `/api/vault/files/${file.id}/data-url` : `/api/vault/files/${file.id}`);
      const data = await response.json();
      if (isImage && data.success && data.data_url) {
        setImagePreviewUrl(data.data_url);
        setSelectedFile(prev => prev?.id === file.id ? {
          ...prev,
          file_type: data.file_type || prev.file_type,
          filename: data.filename || prev.filename
        } : prev);
        setFileContent(null);
      } else if (isImage) {
        setPreviewError(data.error === 'preview_unavailable'
          ? `preview_unavailable: ${data.reason || 'image bytes unavailable'}`
          : (data.error || 'Preview is unavailable'));
        setFileContent(null);
      } else if (data.success && data.file) {
        setSelectedFile(prev => prev?.id === file.id ? { ...prev, ...data.file } : prev);
        setFileContent(data.file.content ?? null);
      } else {
        setPreviewError(data.error || 'Preview is unavailable');
        setFileContent(file.content ?? null);
      }
    } catch (err) {
      console.error('Failed to fetch file preview:', err);
      setPreviewError('Preview request failed');
      setFileContent(file.content ?? null);
    } finally {
      setPreviewLoading(false);
    }
  };

  const getFileIcon = (filename, isFolder = false, fileType = 'text') => {
    return <MaterialVaultIcon filename={filename} isFolder={isFolder} fileType={fileType} />;
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

  const renderMetadataPreview = (file, message = 'Preview content is not available for this file.') => {
    const metadata = parseMetadata(file.metadata);
    const rows = [
      ['Type', file.file_type || '-'],
      ['Construct', file.construct_id || '-'],
      ['Path', file.display_path || file.storage_path || file.filename || '-'],
      ['Size', formatSize(metadata.size)],
      ['Created', formatDate(file.created_at || metadata.migrated_at)]
    ];

    return (
      <div className="metadata-preview">
        <p>{message}</p>
        <div className="metadata-grid">
          {rows.map(([label, value]) => (
            <React.Fragment key={label}>
              <span className="metadata-label">{label}</span>
              <span className="metadata-value">{value}</span>
            </React.Fragment>
          ))}
        </div>
      </div>
    );
  };

  const renderFilePreview = () => {
    if (!selectedFile) return null;
    if (previewLoading) {
      return (
        <div className="preview-state">
          <div className="loading-spinner small"></div>
          <span>Loading preview...</span>
        </div>
      );
    }

    const kind = getPreviewKind(selectedFile);
    const content = fileContent;
    const contentText = getContentText(content);

    if (previewError && !contentText) {
      return renderMetadataPreview(selectedFile, previewError);
    }

    if (kind === 'image') {
      const src = imagePreviewUrl || toDataUrl(content, selectedFile);
      return src
        ? <img className="image-preview" src={src} alt={selectedFile.displayName || selectedFile.filename} />
        : renderMetadataPreview(selectedFile, previewError || 'preview_unavailable: image bytes unavailable');
    }

    if (kind === 'pdf') {
      const src = toDataUrl(content, selectedFile);
      return src
        ? <object className="pdf-preview" data={src} type="application/pdf"><p>PDF preview is unavailable in this browser.</p></object>
        : renderMetadataPreview(selectedFile, 'PDF bytes are not available in the body database row.');
    }

    if (kind === 'audio') {
      const src = toDataUrl(content, selectedFile);
      return src
        ? <audio className="media-preview" controls src={src} />
        : renderMetadataPreview(selectedFile, 'Audio bytes are not available in the body database row.');
    }

    if (kind === 'video') {
      const src = toDataUrl(content, selectedFile);
      return src
        ? <video className="media-preview" controls src={src} />
        : renderMetadataPreview(selectedFile, 'Video bytes are not available in the body database row.');
    }

    if (kind === 'json') {
      return contentText
        ? <pre className="code-preview">{formatJsonPreview(content)}</pre>
        : renderMetadataPreview(selectedFile);
    }

    if (kind === 'csv') {
      const parsed = parseCsvPreview(content);
      if (parsed.headers.length === 0) return renderMetadataPreview(selectedFile);
      return (
        <div className="csv-preview">
          <table>
            <thead>
              <tr>{parsed.headers.map((header, index) => <th key={index}>{header || `Column ${index + 1}`}</th>)}</tr>
            </thead>
            <tbody>
              {parsed.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {parsed.headers.map((_, cellIndex) => <td key={cellIndex}>{row[cellIndex] || ''}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }

    if (kind === 'office') {
      return renderMetadataPreview(selectedFile, 'Office document previews are metadata-only until document extraction is available.');
    }

    if (['markdown', 'code', 'text'].includes(kind)) {
      return contentText
        ? <pre className={kind === 'markdown' ? 'markdown-preview' : 'code-preview'}>{contentText}</pre>
        : renderMetadataPreview(selectedFile);
    }

    return renderMetadataPreview(selectedFile);
  };

  const currentFolder = getCurrentFolder();
  const folderNames = Object.keys(currentFolder.folders).sort();
  const fileList = currentFolder.files.sort((a, b) => 
    (a.displayName || a.filename).localeCompare(b.displayName || b.filename)
  );

  const favorites = [
    { name: 'All Files', icon: '📂', path: [] },
    { name: 'Instances', icon: '🤖', path: ['instances'] },
    { name: 'Library', icon: '📚', path: ['library'] },
    { name: 'Account', icon: '👤', path: ['account'] },
    { name: 'System', icon: '⚙️', path: ['system'] },
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
          {constructs.map((construct, idx) => {
            const constructPath = ['instances', construct.id];
            const isActive = currentPath.length >= 2 && 
              currentPath[0] === 'instances' && currentPath[1] === construct.id;
            const isSyncing = syncingConstruct === construct.id;
            const simDrivePath = ['instances', construct.id, 'simDrive'];
            const isSimDriveActive = currentPath.length >= 3 &&
              currentPath[0] === 'instances' && currentPath[1] === construct.id && currentPath[2] === 'simDrive';
            return (
              <div key={idx} className="construct-block">
                <div className={`sidebar-item construct-row ${isActive ? 'active' : ''}`}>
                  <div className="construct-nav"
                    onClick={() => { setCurrentPath(constructPath); setSelectedFile(null); setFileContent(null); }}
                  >
                    <span 
                      className="construct-dot" 
                      style={{ backgroundColor: construct.color }}
                    ></span>
                    <span className="sidebar-label">{construct.name}</span>
                  </div>
                  <button
                    className="sync-btn"
                    title={`Sync ${construct.id} transcripts to memup capsule`}
                    disabled={isSyncing}
                    onClick={(e) => { e.stopPropagation(); triggerMemupSync(construct.id); }}
                  >
                    {isSyncing ? '...' : '⟳'}
                  </button>
                </div>
                {isActive && (
                  <div className="construct-sublinks">
                    <div
                      className={`sublink ${isSimDriveActive ? 'active' : ''}`}
                      onClick={() => { setCurrentPath(simDrivePath); setSelectedFile(null); setFileContent(null); }}
                    >
                      ◈ SimDrive
                    </div>
                    <div
                      className={`sublink ${currentPath.join('/') === ['instances', construct.id, 'memup'].join('/') ? 'active' : ''}`}
                      onClick={() => { setCurrentPath(['instances', construct.id, 'memup']); setSelectedFile(null); setFileContent(null); }}
                    >
                      ◈ Memup
                    </div>
                    <div
                      className={`sublink ${currentPath.join('/') === ['instances', construct.id, 'identity'].join('/') ? 'active' : ''}`}
                      onClick={() => { setCurrentPath(['instances', construct.id, 'identity']); setSelectedFile(null); setFileContent(null); }}
                    >
                      ◈ Identity
                    </div>
                  </div>
                )}
              </div>
            );
          })}
          {syncResult && (
            <div className={`sync-result ${syncResult.success ? 'sync-success' : 'sync-error'}`}>
              {syncResult.success
                ? `Synced: ${syncResult.entries_added || 0} new, ${syncResult.total_sessions || 0} total sessions`
                : (syncResult.error || 'Sync failed')}
            </div>
          )}
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
            {getActiveConstructId() && (
              <>
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  accept=".zip,.txt,.md,.pdf,.doc,.docx,.json,.csv,.xlsx,.png,.jpg,.jpeg,.svg,.capsule,.py,.js,.yaml,.yml"
                  style={{ display: 'none' }}
                  onChange={(e) => { handleUploadFiles(e.target.files); e.target.value = ''; }}
                />
                <button
                  className="upload-btn"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={uploadState.active}
                  title="Upload files or .zip archive"
                >
                  {uploadState.active ? '...' : '+ Upload'}
                </button>
              </>
            )}
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

        {(uploadState.active || uploadState.result) && (
          <div className="upload-status-bar">
            {uploadState.active && (
              <div className="upload-progress">
                <div className="upload-spinner"></div>
                <span>{uploadState.progress}</span>
              </div>
            )}
            {uploadState.result && (
              <div className={`upload-result ${uploadState.result.success ? 'upload-success' : 'upload-error'}`}>
                {uploadState.result.success
                  ? `${uploadState.result.message || `Uploaded ${uploadState.result.total_files} files`}`
                  : (uploadState.result.error || 'Upload failed')}
              </div>
            )}
          </div>
        )}

        <div
          className={`vault-content ${viewMode} ${dragOver ? 'drag-over' : ''}`}
          onDrop={handleDrop}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
        >
          {dragOver && (
            <div className="drop-overlay">
              <div className="drop-overlay-content">
                <span className="drop-icon">📦</span>
                <span>Drop files or .zip archive here</span>
              </div>
            </div>
          )}
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
              let metadata = file.metadata || {};
              if (typeof metadata === 'string') {
                try { metadata = JSON.parse(metadata); } catch(e) { metadata = {}; }
              }
              if (typeof metadata !== 'object' || metadata === null) metadata = {};
              
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
              <div className="preview-title">
                <h3>{selectedFile.displayName || selectedFile.filename}</h3>
                <span>{getPreviewKind(selectedFile).toUpperCase()}</span>
              </div>
              <button onClick={() => { setSelectedFile(null); setFileContent(null); setImagePreviewUrl(''); setPreviewError(null); }}>×</button>
            </div>
            <div className="preview-content">
              {renderFilePreview()}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default VaultBrowser;
