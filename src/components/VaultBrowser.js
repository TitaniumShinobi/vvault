import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
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

const encodePathSegments = (segments = []) => segments.map((segment) => encodeURIComponent(segment)).join('/');

const parseVaultLocation = (pathname, search) => {
  const segments = String(pathname || '').split('/').filter(Boolean).slice(1);
  const params = new URLSearchParams(search || '');
  if (segments[0] === 'trash') {
    return { mode: 'trash', constructId: '', nodeId: '', legacyPath: ['trash'] };
  }
  if (segments[0] === 'my-ai-files') {
    return { mode: 'home', constructId: '', nodeId: '', legacyPath: [] };
  }
  if (segments[0] === 'folders' && segments[1]) {
    return {
      mode: 'drive',
      constructId: params.get('constructId') || '',
      nodeId: decodeURIComponent(segments[1]),
      workspaceRef: params.get('workspaceRef') || '',
      legacyPath: [],
    };
  }
  if (segments[0] === 'instances' && segments[1]) {
    const constructId = decodeURIComponent(segments[1]);
    return {
      mode: 'drive',
      constructId,
      nodeId: 'root',
      workspaceRef: params.get('workspaceRef') || '',
      legacyPath: ['instances', constructId],
    };
  }
  if (segments[0] === 'browse') {
    return {
      mode: 'legacy',
      constructId: '',
      nodeId: '',
      legacyPath: segments.slice(1).map((segment) => decodeURIComponent(segment)),
    };
  }
  return { mode: 'home', constructId: '', nodeId: '', legacyPath: [] };
};

const vaultLocationForLegacyPath = (segments = []) => (
  segments.length ? `/vault/browse/${encodePathSegments(segments)}` : '/vault'
);

const vaultLocationForDriveFolder = (constructId, nodeId = 'root', workspaceRef = '') => {
  const params = new URLSearchParams();
  if (workspaceRef) params.set('workspaceRef', workspaceRef);
  if (nodeId !== 'root') params.set('constructId', constructId);
  const query = params.toString();
  const path = nodeId === 'root'
    ? `/vault/instances/${encodeURIComponent(constructId)}`
    : `/vault/folders/${encodeURIComponent(nodeId)}`;
  return query ? `${path}?${query}` : path;
};

const materialIconManifest = generateManifest({ activeIconPack: 'react' });
// Keep the browser bundle bounded. Importing the package-wide SVG context emits
// more than a thousand assets and makes production builds crawl on synced disks.
const materialIconContext = require.context(
  'material-icon-theme/icons',
  false,
  /\/(file|folder|markdown|json|image|audio|video|pdf|zip|javascript|typescript|python|css|html|yaml|log|npm|react|word|powerpoint|database|folder-resource|folder-config|folder-database|folder-docs|folder-log)\.svg$/
);
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
  const fallbackFile = iconId?.startsWith('folder') ? 'folder.svg' : 'file.svg';
  return materialIconUrlsByFileName[iconFile] || materialIconUrlsByFileName[fallbackFile] || '';
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

const IMAGE_PREVIEW_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'avif', 'bmp']);

const getPreviewKind = (file = {}) => {
  const filename = file.displayName || file.filename || '';
  const ext = getFileExtension(filename);
  const fileType = (file.file_type || '').toLowerCase();

  if (IMAGE_PREVIEW_EXTENSIONS.has(ext) || fileType.startsWith('image/')) return 'image';
  if (ext === 'pdf' || fileType === 'application/pdf') return 'pdf';
  if (['mp3', 'wav', 'ogg', 'oga', 'opus', 'm4a', 'aac', 'flac'].includes(ext) || fileType.startsWith('audio/')) return 'audio';
  if (['mp4', 'm4v', 'webm', 'ogv', 'mov'].includes(ext) || fileType.startsWith('video/')) return 'video';
  if (ext === 'json' || fileType.includes('json') || ext === 'capsule') return 'json';
  if (ext === 'csv' || fileType.includes('csv')) return 'csv';
  if (['md', 'markdown'].includes(ext) || fileType.includes('markdown')) return 'markdown';
  if (['py', 'js', 'ts', 'jsx', 'tsx', 'css', 'html', 'sql', 'sh', 'yaml', 'yml'].includes(ext)) return 'code';
  if (['txt', 'log'].includes(ext) || fileType.startsWith('text/') || ['text', 'conversation', 'transcript', 'prompt', 'config', 'identity', 'ledger', 'simdrive'].includes(fileType)) return 'text';
  if (['doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx'].includes(ext)) return 'office';
  if (ext === 'zip') return 'archive';
  return 'metadata';
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
  const location = useLocation();
  const navigate = useNavigate();
  const routeState = useMemo(
    () => parseVaultLocation(location.pathname, location.search),
    [location.pathname, location.search]
  );
  const [files, setFiles] = useState([]);
  const [currentPath, setCurrentPath] = useState(routeState.legacyPath);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedFile, setSelectedFile] = useState(null);
  const [fileContent, setFileContent] = useState(null);
  const [mediaPreviewUrl, setMediaPreviewUrl] = useState('');
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState(null);
  const [viewMode, setViewMode] = useState('grid');
  const [constructs, setConstructs] = useState([]);
  const [userInfo, setUserInfo] = useState({ root_label: 'Vault', is_admin: false });
  const [syncingConstruct, setSyncingConstruct] = useState(null);
  const [syncResult, setSyncResult] = useState(null);
  const [uploadState, setUploadState] = useState({ active: false, progress: '', result: null });
  const [dragOver, setDragOver] = useState(false);
  const [driveState, setDriveState] = useState({ loading: false, error: null, parentNode: null, breadcrumbs: [], children: [] });
  const [trashState, setTrashState] = useState({ loading: false, error: null, items: [] });
  const [workspaceState, setWorkspaceState] = useState({ loading: false, error: null, children: [] });
  const [newMenuOpen, setNewMenuOpen] = useState(false);
  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [query, setQuery] = useState('');
  const [selectedNodeIds, setSelectedNodeIds] = useState([]);
  const [nodeMenuId, setNodeMenuId] = useState(null);
  const [renameTarget, setRenameTarget] = useState(null);
  const [renameValue, setRenameValue] = useState('');
  const [moveTarget, setMoveTarget] = useState(null);
  const [movePicker, setMovePicker] = useState({ loading: false, nodeId: 'root', breadcrumbs: [], folders: [], error: null });
  const [lastTrashedNode, setLastTrashedNode] = useState(null);
  const fileInputRef = React.useRef(null);
  const folderInputRef = React.useRef(null);
  const previewRequestIdRef = React.useRef(0);

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

  const fetchWorkspaceRoot = useCallback(async () => {
    setWorkspaceState((previous) => ({ ...previous, loading: true, error: null }));
    try {
      const response = await authFetch('/api/vault/drive/workspace-root');
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.error || data.error_code || 'My AI files could not be loaded');
      }
      setWorkspaceState({
        loading: false,
        error: null,
        children: Array.isArray(data.children) ? data.children : [],
      });
    } catch (err) {
      setWorkspaceState({ loading: false, error: err.message || 'My AI files could not be loaded', children: [] });
    }
  }, []);

  const fetchDriveChildren = useCallback(async ({ constructId, nodeId, workspaceRef }) => {
    if (!constructId || !workspaceRef) return;
    setDriveState((previous) => ({ ...previous, loading: true, error: null }));
    try {
      const params = new URLSearchParams({ constructId, parentNodeId: nodeId || 'root', workspaceRef });
      const response = await authFetch(`/api/vault/drive/children?${params.toString()}`);
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.error || data.error_code || 'Folder could not be loaded');
      }
      setDriveState({
        loading: false,
        error: null,
        parentNode: data.parentNode || null,
        breadcrumbs: Array.isArray(data.breadcrumbs) ? data.breadcrumbs : [],
        children: Array.isArray(data.children) ? data.children : [],
        cacheState: data.cacheState || 'fresh',
        refreshing: Boolean(data.refreshing),
      });
    } catch (err) {
      setDriveState((previous) => ({ ...previous, loading: false, error: err.message || 'Folder could not be loaded' }));
    }
  }, []);

  const fetchTrash = useCallback(async () => {
    setTrashState((previous) => ({ ...previous, loading: true, error: null }));
    try {
      const response = await authFetch('/api/vault/drive/trash');
      const data = await response.json();
      if (!response.ok || !data.success) throw new Error(data.error || data.error_code || 'Trash could not be loaded');
      setTrashState({ loading: false, error: null, items: Array.isArray(data.items) ? data.items : [] });
    } catch (err) {
      setTrashState({ loading: false, error: err.message || 'Trash could not be loaded', items: [] });
    }
  }, []);

  useEffect(() => {
    fetchUserInfo();
    fetchConstructs();
    if (routeState.mode === 'trash') fetchTrash();
    else if (routeState.mode === 'home') fetchWorkspaceRoot();
    else if (!['drive', 'home'].includes(routeState.mode)) fetchFiles();
  }, [fetchUserInfo, fetchFiles, fetchConstructs, fetchTrash, fetchWorkspaceRoot, routeState.mode]);

  useEffect(() => {
    setCurrentPath(routeState.mode === 'drive'
      ? ['instances', routeState.constructId, ...driveState.breadcrumbs.map((item) => item.name)]
      : routeState.legacyPath);
  }, [routeState.mode, routeState.constructId, routeState.legacyPath.join('/'), driveState.breadcrumbs]);

  useEffect(() => {
    if (routeState.mode === 'drive' && routeState.constructId) {
      setSelectedNodeIds([]);
      setNodeMenuId(null);
      fetchDriveChildren({
        constructId: routeState.constructId,
        nodeId: routeState.nodeId || 'root',
        workspaceRef: routeState.workspaceRef,
      });
    }
  }, [routeState.mode, routeState.constructId, routeState.nodeId, fetchDriveChildren]);

  useEffect(() => () => {
    if (mediaPreviewUrl) URL.revokeObjectURL(mediaPreviewUrl);
  }, [mediaPreviewUrl]);

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
    if (routeState.mode === 'drive') return routeState.constructId || null;
    if (currentPath.length >= 2 && currentPath[0] === 'instances') {
      return currentPath[1];
    }
    return null;
  };

  const canUploadToCurrentDriveFolder = routeState.mode === 'drive'
    && Boolean(routeState.constructId)
    && Boolean(routeState.nodeId)
    && routeState.nodeId !== 'root';

  const handleUploadFiles = async (fileList, { folderUpload = false } = {}) => {
    const constructId = getActiveConstructId();
    if (!constructId) {
      setUploadState({ active: false, progress: '', result: { success: false, error: 'Navigate to a construct folder first' } });
      return;
    }
    if (!fileList || fileList.length === 0) return;

    setUploadState({ active: true, progress: 'Preparing upload...', result: null });

    const formData = new FormData();
    formData.append('construct_id', constructId);
    if (routeState.mode === 'drive') {
      formData.append('destinationFolderId', routeState.nodeId || 'root');
    }
    for (let i = 0; i < fileList.length; i++) {
      formData.append('files', fileList[i]);
      const relativePath = folderUpload
        ? (fileList[i].webkitRelativePath || fileList[i].name)
        : fileList[i].name;
      formData.append('relative_paths', relativePath);
    }

    const totalSize = Array.from(fileList).reduce((s, f) => s + f.size, 0);
    const sizeMB = (totalSize / (1024 * 1024)).toFixed(1);
    setUploadState({ active: true, progress: `Uploading ${fileList.length} file(s) (${sizeMB} MB)...`, result: null });

    try {
      const response = await fetch('/api/vault/knowledge-files/upload', {
        method: 'POST',
        credentials: 'same-origin',
        body: formData,
      });
      const data = await response.json();
      setUploadState({ active: false, progress: '', result: data });
      if (data.success) {
        if (routeState.mode === 'drive') {
          fetchDriveChildren({ constructId, nodeId: routeState.nodeId || 'root' });
        } else {
          fetchFiles();
        }
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

  const navigateToFolder = (folder) => {
    previewRequestIdRef.current += 1;
    if (routeState.mode === 'drive' && folder?.nodeId) {
      navigate(vaultLocationForDriveFolder(routeState.constructId, folder.nodeId, routeState.workspaceRef));
    } else if (['home', 'my-ai-files'].includes(routeState.mode) && folder?.constructId) {
      navigate(vaultLocationForDriveFolder(folder.constructId, 'root', folder.workspaceRef));
    } else {
      const folderName = typeof folder === 'string' ? folder : folder?.name;
      navigate(vaultLocationForLegacyPath([...currentPath, folderName]));
    }
    setSelectedFile(null);
    setFileContent(null);
    setMediaPreviewUrl('');
    setPreviewError(null);
  };

  const navigateBack = () => {
    previewRequestIdRef.current += 1;
    navigate(-1);
    setSelectedFile(null);
    setFileContent(null);
    setMediaPreviewUrl('');
    setPreviewError(null);
  };

  const navigateHome = () => {
    previewRequestIdRef.current += 1;
    navigate('/vault');
    setSelectedFile(null);
    setFileContent(null);
    setMediaPreviewUrl('');
    setPreviewError(null);
  };

  const navigateToBreadcrumb = (index) => {
    previewRequestIdRef.current += 1;
    if (routeState.mode === 'drive') {
      if (index === 0) navigate('/vault');
      else if (index === 1) navigate(vaultLocationForDriveFolder(routeState.constructId, 'root', routeState.workspaceRef));
      else {
        const breadcrumb = driveState.breadcrumbs[index - 2];
        if (breadcrumb?.nodeId) navigate(vaultLocationForDriveFolder(routeState.constructId, breadcrumb.nodeId, routeState.workspaceRef));
      }
    } else {
      navigate(vaultLocationForLegacyPath(currentPath.slice(0, index + 1)));
    }
    setSelectedFile(null);
    setFileContent(null);
    setMediaPreviewUrl('');
    setPreviewError(null);
  };

  const navigateToPath = (path) => {
    if (path.length === 1 && path[0] === 'trash') {
      navigate('/vault/trash');
    } else if (path.length === 2 && path[0] === 'instances') {
      navigate(vaultLocationForDriveFolder(path[1], 'root'));
    } else {
      navigate(vaultLocationForLegacyPath(path));
    }
    setSelectedFile(null);
    setFileContent(null);
  };

  const createFolder = async (event) => {
    event.preventDefault();
    const name = newFolderName.trim();
    const constructId = getActiveConstructId();
    if (!name || !constructId || routeState.mode !== 'drive') return;
    setCreatingFolder(true);
    try {
      const response = await authFetch('/api/vault/drive/folders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          constructId,
          parentNodeId: routeState.nodeId || 'root',
          name,
        }),
      });
      const data = await response.json();
      if (!response.ok || !data.success) throw new Error(data.error || data.error_code || 'Folder could not be created');
      setNewFolderOpen(false);
      setNewFolderName('');
      setUploadState({ active: false, progress: '', result: { success: true, message: `Folder “${name}” created` } });
      await fetchDriveChildren({ constructId, nodeId: routeState.nodeId || 'root' });
    } catch (err) {
      setUploadState({ active: false, progress: '', result: { success: false, error: err.message || 'Folder could not be created' } });
    } finally {
      setCreatingFolder(false);
    }
  };

  const patchDriveNode = async (nodeId, body) => {
    const response = await authFetch(`/api/vault/drive/nodes/${encodeURIComponent(nodeId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ constructId: routeState.constructId, ...body }),
    });
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.error || data.error_code || 'Item could not be updated');
    return data;
  };

  const loadMoveDestination = async (nodeId = 'root') => {
    setMovePicker((previous) => ({ ...previous, loading: true, error: null }));
    try {
      const query = new URLSearchParams({ constructId: routeState.constructId, parentNodeId: nodeId });
      const response = await authFetch(`/api/vault/drive/children?${query.toString()}`);
      const data = await response.json();
      if (!response.ok || !data.success) throw new Error(data.error || data.error_code || 'Folder could not be loaded');
      setMovePicker({
        loading: false,
        nodeId,
        breadcrumbs: Array.isArray(data.breadcrumbs) ? data.breadcrumbs : [],
        folders: (Array.isArray(data.children) ? data.children : []).filter((item) => item.type === 'folder'),
        error: null,
      });
    } catch (err) {
      setMovePicker((previous) => ({ ...previous, loading: false, error: err.message || 'Folder could not be loaded' }));
    }
  };

  const openMovePicker = (node) => {
    setNodeMenuId(null);
    setMoveTarget(node);
    loadMoveDestination('root');
  };

  const selectedDriveNodes = () => driveState.children.filter((item) => selectedNodeIds.includes(item.nodeId));

  const openBatchMovePicker = () => {
    const nodes = selectedDriveNodes();
    if (!nodes.length) return;
    setMoveTarget({ nodeId: '__batch__', name: `${nodes.length} selected items`, parentNodeId: null, batchNodeIds: nodes.map((item) => item.nodeId) });
    loadMoveDestination('root');
  };

  const moveDriveNode = async () => {
    if (!moveTarget?.nodeId) return;
    setCreatingFolder(true);
    try {
      if (Array.isArray(moveTarget.batchNodeIds)) {
        const response = await authFetch('/api/vault/drive/batch/move', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ constructId: routeState.constructId, nodeIds: moveTarget.batchNodeIds, parentNodeId: movePicker.nodeId }),
        });
        const data = await response.json();
        if (!response.ok || !data.success) throw new Error(data.error || data.error_code || 'Items could not be moved');
        setSelectedNodeIds([]);
      } else {
        await patchDriveNode(moveTarget.nodeId, { parentNodeId: movePicker.nodeId });
      }
      setMoveTarget(null);
      await fetchDriveChildren({ constructId: routeState.constructId, nodeId: routeState.nodeId || 'root' });
    } catch (err) {
      setMovePicker((previous) => ({ ...previous, error: err.message || 'Item could not be moved' }));
    } finally {
      setCreatingFolder(false);
    }
  };

  const renameDriveNode = async (event) => {
    event.preventDefault();
    const name = renameValue.trim();
    if (!renameTarget?.nodeId || !name) return;
    setCreatingFolder(true);
    try {
      await patchDriveNode(renameTarget.nodeId, { name });
      setRenameTarget(null);
      setRenameValue('');
      await fetchDriveChildren({ constructId: routeState.constructId, nodeId: routeState.nodeId || 'root' });
    } catch (err) {
      setUploadState({ active: false, progress: '', result: { success: false, error: err.message || 'Item could not be renamed' } });
    } finally {
      setCreatingFolder(false);
    }
  };

  const trashDriveNode = async (node) => {
    if (!node?.nodeId) return;
    setNodeMenuId(null);
    try {
      const response = await authFetch('/api/vault/drive/batch/trash', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ constructId: routeState.constructId, nodeIds: [node.nodeId] }),
      });
      const data = await response.json();
      if (!response.ok || !data.success) throw new Error(data.error || data.error_code || 'Item could not be moved to trash');
      setLastTrashedNode(node);
      setSelectedNodeIds((ids) => ids.filter((id) => id !== node.nodeId));
      await fetchDriveChildren({ constructId: routeState.constructId, nodeId: routeState.nodeId || 'root' });
    } catch (err) {
      setUploadState({ active: false, progress: '', result: { success: false, error: err.message || 'Item could not be moved to trash' } });
    }
  };

  const trashSelectedNodes = async () => {
    const nodes = selectedDriveNodes();
    if (!nodes.length) return;
    try {
      const response = await authFetch('/api/vault/drive/batch/trash', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ constructId: routeState.constructId, nodeIds: nodes.map((item) => item.nodeId) }),
      });
      const data = await response.json();
      if (!response.ok || !data.success) throw new Error(data.error || data.error_code || 'Selected items could not be moved to Trash');
      setLastTrashedNode({ nodeId: null, name: `${data.count} selected items`, batch: nodes });
      setSelectedNodeIds([]);
      setUploadState({ active: false, progress: '', result: { success: true, message: `${data.count} items moved to Trash` } });
      await fetchDriveChildren({ constructId: routeState.constructId, nodeId: routeState.nodeId || 'root' });
    } catch (err) {
      setUploadState({ active: false, progress: '', result: { success: false, error: err.message } });
    }
  };

  const restoreLastTrashedNode = async () => {
    if (!lastTrashedNode?.nodeId && !lastTrashedNode?.batch) return;
    try {
      const response = await authFetch('/api/vault/drive/batch/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ constructId: routeState.constructId, nodeIds: lastTrashedNode.batch ? lastTrashedNode.batch.map((item) => item.nodeId) : [lastTrashedNode.nodeId] }),
      });
      const data = await response.json();
      if (!response.ok || !data.success) throw new Error(data.error || data.error_code || 'Item could not be restored');
      setLastTrashedNode(null);
      await fetchDriveChildren({ constructId: routeState.constructId, nodeId: routeState.nodeId || 'root' });
    } catch (err) {
      setUploadState({ active: false, progress: '', result: { success: false, error: err.message || 'Item could not be restored' } });
    }
  };

  const runTrashActionByConstruct = async (items, endpoint, extra = {}) => {
    const groups = items.reduce((result, item) => {
      (result[item.constructId] ||= []).push(item.nodeId); return result;
    }, {});
    for (const [constructId, nodeIds] of Object.entries(groups)) {
      const response = await authFetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ constructId, nodeIds, ...extra }) });
      const data = await response.json();
      if (!response.ok || !data.success) throw new Error(data.error || data.error_code || 'Trash operation failed');
    }
    setSelectedNodeIds([]);
    await fetchTrash();
  };

  const restoreTrashItems = async (items) => {
    try { await runTrashActionByConstruct(items, '/api/vault/drive/batch/restore'); }
    catch (err) { setUploadState({ active:false, progress:'', result:{ success:false, error:err.message } }); }
  };

  const permanentlyDeleteTrashItems = async (items, emptyTrash = false) => {
    if (!items.length || !window.confirm(emptyTrash ? 'Permanently delete every item in Trash? This cannot be undone.' : `Permanently delete ${items.length} selected item(s)? This cannot be undone.`)) return;
    try {
      await runTrashActionByConstruct(items, '/api/vault/drive/batch/permanent-delete', { confirmation: 'PERMANENTLY DELETE', emptyTrash });
      setUploadState({ active:false, progress:'', result:{ success:true, message: emptyTrash ? 'Trash emptied' : 'Items permanently deleted' } });
    } catch (err) { setUploadState({ active:false, progress:'', result:{ success:false, error:err.message } }); }
  };

  const downloadDriveFile = async (file) => {
    const response = await authFetch(`/api/vault/drive/files/${encodeURIComponent(file.fileId || file.nodeId)}/download`);
    if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.error || 'Download failed'); }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob); const anchor = document.createElement('a');
    anchor.href = url; anchor.download = file.name || file.displayName || 'download'; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const toggleNodeSelection = (event, nodeId) => {
    event.stopPropagation();
    setSelectedNodeIds((ids) => ids.includes(nodeId) ? ids.filter((id) => id !== nodeId) : [...ids, nodeId]);
  };

  const selectFile = async (file) => {
    const requestId = previewRequestIdRef.current + 1;
    previewRequestIdRef.current = requestId;
    setSelectedFile(file);
    setFileContent(file.content ?? null);
    setMediaPreviewUrl('');
    setPreviewError(null);
    setPreviewLoading(true);

    try {
      const previewKind = getPreviewKind(file);
      const isMedia = ['image', 'pdf', 'audio', 'video'].includes(previewKind);
      const isArchive = previewKind === 'archive';
      const response = await authFetch(
        isMedia
          ? `/api/vault/files/${file.id}/media`
          : isArchive
            ? `/api/vault/files/${file.id}/archive`
            : `/api/vault/files/${file.id}`
      );
      if (isMedia && response.ok) {
        const blob = await response.blob();
        const objectUrl = URL.createObjectURL(blob);
        if (previewRequestIdRef.current !== requestId) {
          URL.revokeObjectURL(objectUrl);
          return;
        }
        setMediaPreviewUrl(objectUrl);
        setSelectedFile(prev => prev?.id === file.id ? {
          ...prev,
          file_type: blob.type || prev.file_type
        } : prev);
        setFileContent(null);
        return;
      }
      const data = await response.json();
      if (previewRequestIdRef.current !== requestId) return;
      if (isArchive && data.success) {
        setFileContent(data);
        return;
      }
      if (isMedia || isArchive) {
        if (previewKind === 'pdf') {
          try {
            const detailResponse = await authFetch(`/api/vault/files/${file.id}`);
            const detailData = await detailResponse.json();
            if (previewRequestIdRef.current !== requestId) return;
            if (detailData.success && detailData.file) {
              setSelectedFile(prev => prev?.id === file.id ? { ...prev, ...detailData.file } : prev);
              setFileContent(detailData.file.content ?? null);
            } else {
              setFileContent(null);
            }
          } catch (_detailError) {
            setFileContent(null);
          }
        } else {
          setFileContent(null);
        }
        setPreviewError(data.error === 'preview_unavailable'
          ? `preview_unavailable: ${data.reason || 'media bytes unavailable'}`
          : (data.error || 'Preview is unavailable'));
      } else if (data.success && data.file) {
        setSelectedFile(prev => prev?.id === file.id ? { ...prev, ...data.file } : prev);
        setFileContent(data.file.content ?? null);
      } else {
        setPreviewError(data.error || 'Preview is unavailable');
        setFileContent(file.content ?? null);
      }
    } catch (err) {
      if (previewRequestIdRef.current !== requestId) return;
      console.error('Failed to fetch file preview:', err);
      setPreviewError('Preview request failed');
      setFileContent(file.content ?? null);
    } finally {
      if (previewRequestIdRef.current === requestId) setPreviewLoading(false);
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
      const src = mediaPreviewUrl || toDataUrl(content, selectedFile);
      return src
        ? <div className="media-preview-shell"><img className="image-preview" src={src} alt={selectedFile.displayName || selectedFile.filename} /><a className="preview-open-link" href={src} target="_blank" rel="noreferrer">Open full size</a></div>
        : renderMetadataPreview(selectedFile, previewError || 'preview_unavailable: image bytes unavailable');
    }

    if (kind === 'pdf') {
      const src = mediaPreviewUrl || toDataUrl(content, selectedFile);
      if (src) {
        return <div className="media-preview-shell"><object className="pdf-preview" data={src} type="application/pdf"><p>PDF preview is unavailable in this browser.</p></object><a className="preview-open-link" href={src} target="_blank" rel="noreferrer">Open PDF</a></div>;
      }
      if (contentText) {
        return (
          <div className="document-text-fallback">
            <p>{previewError || 'PDF bytes are unavailable; showing the canonical extracted text.'}</p>
            <pre className="code-preview">{contentText}</pre>
          </div>
        );
      }
      return renderMetadataPreview(selectedFile, previewError || 'PDF bytes are unavailable.');
    }

    if (kind === 'audio') {
      const src = mediaPreviewUrl || toDataUrl(content, selectedFile);
      return src
        ? <div className="media-preview-shell audio"><audio className="media-preview" controls preload="metadata" src={src}>Your browser cannot play this audio.</audio><a className="preview-open-link" href={src} target="_blank" rel="noreferrer">Open audio</a></div>
        : renderMetadataPreview(selectedFile, 'Audio bytes are not available in the body database row.');
    }

    if (kind === 'video') {
      const src = mediaPreviewUrl || toDataUrl(content, selectedFile);
      return src
        ? <div className="media-preview-shell"><video className="media-preview video" controls playsInline preload="metadata" src={src}>Your browser cannot play this video.</video><a className="preview-open-link" href={src} target="_blank" rel="noreferrer">Open video</a></div>
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

    if (kind === 'archive') {
      const entries = content && typeof content === 'object' ? content.entries : null;
      if (!Array.isArray(entries)) {
        return renderMetadataPreview(selectedFile, previewError || 'Archive directory is unavailable.');
      }
      return (
        <div className="archive-preview">
          <p>{content.entry_count} archive entr{content.entry_count === 1 ? 'y' : 'ies'}{content.truncated ? ' (showing the first entries)' : ''}</p>
          <table>
            <thead><tr><th>Name</th><th>Size</th></tr></thead>
            <tbody>
              {entries.map((entry, index) => (
                <tr key={`${entry.name}-${index}`}>
                  <td>{entry.is_directory ? '📁 ' : ''}{entry.name}</td>
                  <td>{entry.is_directory ? '-' : formatSize(entry.size)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }

    if (['markdown', 'code', 'text'].includes(kind)) {
      return contentText
        ? <pre className={kind === 'markdown' ? 'markdown-preview' : 'code-preview'}>{contentText}</pre>
        : renderMetadataPreview(selectedFile);
    }

    return renderMetadataPreview(selectedFile);
  };

  const currentFolder = getCurrentFolder();
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const activeChildren = routeState.mode === 'trash' ? trashState.items : driveState.children;
  const driveFolders = activeChildren
    .filter((child) => child.nodeType === 'folder' && (routeState.mode === 'trash' ? child.trashed : !child.trashed))
    .filter((child) => !normalizedQuery || child.name.toLocaleLowerCase().includes(normalizedQuery))
    .sort((a, b) => a.name.localeCompare(b.name));
  const driveFiles = activeChildren
    .filter((child) => child.nodeType === 'file' && (routeState.mode === 'trash' ? child.trashed : !child.trashed))
    .filter((child) => !normalizedQuery || child.name.toLocaleLowerCase().includes(normalizedQuery))
    .map((child) => ({
      ...child,
      id: child.fileId || child.nodeId,
      filename: child.name,
      displayName: child.name,
      construct_id: child.constructId,
      file_type: child.contentType,
      created_at: child.createdAt,
      updated_at: child.updatedAt,
      metadata: { size: child.sizeBytes },
    }))
    .sort((a, b) => a.displayName.localeCompare(b.displayName));
  const legacyFolderNames = Object.keys(currentFolder.folders)
    .filter((name) => !normalizedQuery || name.toLocaleLowerCase().includes(normalizedQuery))
    .sort();
  const legacyFiles = currentFolder.files
    .filter((file) => !normalizedQuery || (file.displayName || file.filename || '').toLocaleLowerCase().includes(normalizedQuery))
    .sort((a, b) => (a.displayName || a.filename).localeCompare(b.displayName || b.filename));
  const folderEntries = routeState.mode === 'home'
    ? workspaceState.children
        .filter((child) => child.nodeType === 'folder')
        .filter((child) => !normalizedQuery || child.name.toLocaleLowerCase().includes(normalizedQuery))
        .map((child) => ({ ...child, updatedAt: child.updatedAt || null }))
    : ['drive', 'trash'].includes(routeState.mode)
      ? driveFolders
      : legacyFolderNames.map((name) => ({ name }));
  const fileList = routeState.mode === 'home'
    ? []
    : ['drive', 'trash'].includes(routeState.mode) ? driveFiles : legacyFiles;

  const favorites = [
    { name: 'All Files', icon: '📂', path: [] },
    { name: 'Instances', icon: '🤖', path: ['instances'] },
    { name: 'Library', icon: '📚', path: ['library'] },
    { name: 'Account', icon: '👤', path: ['account'] },
    { name: 'System', icon: '⚙️', path: ['system'] },
    { name: 'Trash', icon: '🗑️', path: ['trash'] },
  ];

  if (loading && !['drive', 'trash', 'home', 'my-ai-files'].includes(routeState.mode)) {
    return (
      <div className="vault-browser">
        <div className="vault-loading">
          <div className="loading-spinner"></div>
          <p>Loading vault...</p>
        </div>
      </div>
    );
  }

  if (error && !['drive', 'trash', 'home', 'my-ai-files'].includes(routeState.mode)) {
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
        {routeState.mode !== 'legacy' ? (
          <div className="sidebar-section drive-primary-navigation" aria-label="Vault navigation">
            <button className={`sidebar-item drive-nav-button ${routeState.mode === 'home' ? 'active' : ''}`} onClick={navigateHome}>
              <span className="sidebar-icon">⌂</span>
              <span className="sidebar-label">Home</span>
            </button>
            <button className={`sidebar-item drive-nav-button ${routeState.mode === 'trash' ? 'active' : ''}`} onClick={() => navigate('/vault/trash')}>
              <span className="sidebar-icon">♲</span>
              <span className="sidebar-label">Trash</span>
            </button>
          </div>
        ) : (
          <div className="sidebar-section">
            <h3>FAVORITES</h3>
            {favorites.map((fav, idx) => (
              <div
                key={idx}
                className={`sidebar-item ${JSON.stringify(currentPath) === JSON.stringify(fav.path) ? 'active' : ''}`}
                onClick={() => navigateToPath(fav.path)}
              >
                <span className="sidebar-icon">{fav.icon}</span>
                <span className="sidebar-label">{fav.name}</span>
              </div>
            ))}
          </div>
        )}

        {routeState.mode === 'legacy' && <div className="sidebar-section">
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
                    onClick={() => navigateToPath(constructPath)}
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
                      onClick={() => navigateToPath(simDrivePath)}
                    >
                      ◈ SimDrive
                    </div>
                    <div
                      className={`sublink ${currentPath.join('/') === ['instances', construct.id, 'memup'].join('/') ? 'active' : ''}`}
                      onClick={() => navigateToPath(['instances', construct.id, 'memup'])}
                    >
                      ◈ Memup
                    </div>
                    <div
                      className={`sublink ${currentPath.join('/') === ['instances', construct.id, 'identity'].join('/') ? 'active' : ''}`}
                      onClick={() => navigateToPath(['instances', construct.id, 'identity'])}
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
        </div>}
      </div>

      <div className="vault-main">
        <div className="vault-toolbar">
          <div className="breadcrumb">
            <span className="breadcrumb-icon">{userInfo.is_admin ? '🔐' : '🔒'}</span>
            <span
              className="breadcrumb-item clickable"
              onClick={navigateHome}
            >
              {routeState.mode === 'home' ? 'Home' : userInfo.root_label}
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
            {routeState.mode === 'drive' && selectedNodeIds.length > 0 && (
              <div className="drive-selection-actions" role="toolbar" aria-label="Selected item actions">
                <span>{selectedNodeIds.length} selected</span>
                <button onClick={openBatchMovePicker}>Move</button>
                <button className="danger" onClick={trashSelectedNodes}>Delete</button>
                <button onClick={() => setSelectedNodeIds([])}>Clear</button>
              </div>
            )}
            {routeState.mode === 'trash' && (
              <div className="drive-selection-actions" role="toolbar" aria-label="Trash actions">
                <span>{trashState.items.length} item{trashState.items.length === 1 ? '' : 's'}</span>
                <button disabled={!selectedNodeIds.length} onClick={() => restoreTrashItems(trashState.items.filter((item) => selectedNodeIds.includes(item.nodeId)))}>Restore</button>
                <button disabled={!trashState.items.length} onClick={() => restoreTrashItems(trashState.items)}>Restore all</button>
                <button className="danger" disabled={!selectedNodeIds.length} onClick={() => permanentlyDeleteTrashItems(trashState.items.filter((item) => selectedNodeIds.includes(item.nodeId)))}>Permanent delete</button>
                <button className="danger" disabled={!trashState.items.length} onClick={() => permanentlyDeleteTrashItems(trashState.items, true)}>Empty trash</button>
              </div>
            )}
            {getActiveConstructId() && routeState.mode === 'drive' && (
              <div className="new-action-shell">
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  accept=".zip,.txt,.md,.pdf,.doc,.docx,.json,.csv,.xlsx,.png,.jpg,.jpeg,.svg,.capsule,.py,.js,.yaml,.yml"
                  style={{ display: 'none' }}
                  onChange={(e) => { handleUploadFiles(e.target.files); e.target.value = ''; }}
                />
                <input
                  ref={folderInputRef}
                  type="file"
                  multiple
                  webkitdirectory=""
                  directory=""
                  style={{ display: 'none' }}
                  onChange={(e) => { handleUploadFiles(e.target.files, { folderUpload: true }); e.target.value = ''; }}
                />
                <button
                  className="new-btn"
                  onClick={() => setNewMenuOpen((open) => !open)}
                  disabled={uploadState.active}
                  aria-haspopup="menu"
                  aria-expanded={newMenuOpen}
                >
                  {uploadState.active ? 'Working…' : '+ New'}
                </button>
                {newMenuOpen && (
                  <div className="new-menu" role="menu">
                    <button role="menuitem" onClick={() => { setNewMenuOpen(false); setNewFolderOpen(true); }}>New folder</button>
                    <div className="new-menu-separator" />
                    <button
                      role="menuitem"
                      disabled={!canUploadToCurrentDriveFolder}
                      title={canUploadToCurrentDriveFolder ? 'Upload files here' : 'Open assets, documents, or a transcript provider folder first'}
                      onClick={() => { setNewMenuOpen(false); fileInputRef.current?.click(); }}
                    >File upload</button>
                    <button
                      role="menuitem"
                      disabled={!canUploadToCurrentDriveFolder}
                      title={canUploadToCurrentDriveFolder ? 'Upload a folder here' : 'Open assets, documents, or a transcript provider folder first'}
                      onClick={() => { setNewMenuOpen(false); folderInputRef.current?.click(); }}
                    >Folder upload</button>
                  </div>
                )}
              </div>
            )}
            <input 
              type="text" 
              placeholder="Search files..." 
              className="search-input"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
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

        {newFolderOpen && (
          <div className="drive-modal-backdrop" role="presentation" onMouseDown={(event) => {
            if (event.target === event.currentTarget && !creatingFolder) setNewFolderOpen(false);
          }}>
            <form className="drive-modal" onSubmit={createFolder} aria-label="Create new folder">
              <h2>New folder</h2>
              <input
                autoFocus
                value={newFolderName}
                onChange={(event) => setNewFolderName(event.target.value)}
                placeholder="Untitled folder"
                aria-label="Folder name"
              />
              <div className="drive-modal-actions">
                <button type="button" onClick={() => setNewFolderOpen(false)} disabled={creatingFolder}>Cancel</button>
                <button type="submit" disabled={!newFolderName.trim() || creatingFolder}>{creatingFolder ? 'Creating…' : 'Create'}</button>
              </div>
            </form>
          </div>
        )}

        {renameTarget && (
          <div className="drive-modal-backdrop" role="presentation" onMouseDown={(event) => {
            if (event.target === event.currentTarget && !creatingFolder) setRenameTarget(null);
          }}>
            <form className="drive-modal" onSubmit={renameDriveNode} aria-label={`Rename ${renameTarget.name}`}>
              <h2>Rename</h2>
              <input
                autoFocus
                value={renameValue}
                onChange={(event) => setRenameValue(event.target.value)}
                aria-label="New name"
              />
              <div className="drive-modal-actions">
                <button type="button" onClick={() => setRenameTarget(null)} disabled={creatingFolder}>Cancel</button>
                <button type="submit" disabled={!renameValue.trim() || creatingFolder}>{creatingFolder ? 'Renaming…' : 'Save'}</button>
              </div>
            </form>
          </div>
        )}

        {moveTarget && (
          <div className="drive-modal-backdrop" role="presentation" onMouseDown={(event) => {
            if (event.target === event.currentTarget && !creatingFolder) setMoveTarget(null);
          }}>
            <div className="drive-modal drive-move-modal" role="dialog" aria-modal="true" aria-label={`Move ${moveTarget.name}`}>
              <h2>Move “{moveTarget.name}”</h2>
              <div className="move-breadcrumbs">
                <button type="button" onClick={() => loadMoveDestination('root')}>{routeState.constructId}</button>
                {movePicker.breadcrumbs.map((item) => (
                  <React.Fragment key={item.nodeId}>
                    <span>/</span>
                    <button type="button" onClick={() => loadMoveDestination(item.nodeId)}>{item.name}</button>
                  </React.Fragment>
                ))}
              </div>
              <div className="move-folder-list">
                {movePicker.loading && <p>Loading folders…</p>}
                {movePicker.error && <p className="move-error">{movePicker.error}</p>}
                {!movePicker.loading && !movePicker.error && movePicker.folders.map((folder) => (
                  <button type="button" key={folder.nodeId} onClick={() => loadMoveDestination(folder.nodeId)}>
                    <span>{getFileIcon(folder.name, true)}</span>
                    <span>{folder.name}</span>
                    <span>›</span>
                  </button>
                ))}
                {!movePicker.loading && !movePicker.error && movePicker.folders.length === 0 && <p>No folders here</p>}
              </div>
              <div className="drive-modal-actions">
                <button type="button" onClick={() => setMoveTarget(null)} disabled={creatingFolder}>Cancel</button>
                <button
                  type="button"
                  onClick={moveDriveNode}
                  disabled={creatingFolder || movePicker.loading || movePicker.nodeId === moveTarget.parentNodeId}
                >{creatingFolder ? 'Moving…' : 'Move here'}</button>
              </div>
            </div>
          </div>
        )}

        {lastTrashedNode && (
          <div className="drive-undo-toast" role="status">
            <span>“{lastTrashedNode.name}” moved to trash</span>
            <button onClick={restoreLastTrashedNode}>Undo</button>
            <button aria-label="Dismiss" onClick={() => setLastTrashedNode(null)}>×</button>
          </div>
        )}

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
            
            {folderEntries.map((folder, idx) => (
              <div 
                key={`folder-${folder.nodeId || folder.name || idx}`}
                className={`file-row folder ${selectedNodeIds.includes(folder.nodeId) ? 'selected' : ''}`}
                onDoubleClick={() => { if (routeState.mode !== 'trash') navigateToFolder(folder); }}
              >
                <span className="col-name">
                  {['drive', 'trash'].includes(routeState.mode) && (
                    <input
                      type="checkbox"
                      className="node-checkbox"
                      checked={selectedNodeIds.includes(folder.nodeId)}
                      onChange={(event) => toggleNodeSelection(event, folder.nodeId)}
                      onDoubleClick={(event) => event.stopPropagation()}
                      aria-label={`Select folder ${folder.name}`}
                    />
                  )}
                  <span className="file-icon">{getFileIcon(folder.name, true)}</span>
                  <span className="file-name">{folder.name}</span>
                </span>
                <span className="col-construct">{routeState.mode === 'trash' ? (folder.originalPath || folder.constructId) : '-'}</span>
                <span className="col-date">{formatDate(routeState.mode === 'trash' ? folder.deletedAt : folder.updatedAt)}</span>
                <span className="col-size node-actions-cell">
                  {routeState.mode === 'trash' ? (
                    <div className="trash-row-actions">
                      <button onClick={() => restoreTrashItems([folder])}>Restore</button>
                      <button className="danger" onClick={() => permanentlyDeleteTrashItems([folder])}>Permanent delete</button>
                    </div>
                  ) : routeState.mode === 'drive' && !folder.protected ? (
                    <>
                      <button className="node-more-btn" aria-label={`More actions for ${folder.name}`} onClick={(event) => { event.stopPropagation(); setNodeMenuId(nodeMenuId === folder.nodeId ? null : folder.nodeId); }}>⋮</button>
                      {nodeMenuId === folder.nodeId && (
                        <div className="node-menu" role="menu">
                          <button role="menuitem" onClick={() => { setNodeMenuId(null); setRenameTarget(folder); setRenameValue(folder.name); }}>Rename</button>
                          <button role="menuitem" onClick={() => openMovePicker(folder)}>Move</button>
                          <button role="menuitem" className="danger" onClick={() => trashDriveNode(folder)}>Move to trash</button>
                        </div>
                      )}
                    </>
                  ) : '-'}
                </span>
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
                  className={`file-row ${selectedFile?.id === file.id || selectedNodeIds.includes(file.nodeId) ? 'selected' : ''}`}
                  onClick={() => { if (routeState.mode !== 'trash') selectFile(file); }}
                >
                  <span className="col-name">
                    {['drive', 'trash'].includes(routeState.mode) && (
                      <input
                        type="checkbox"
                        className="node-checkbox"
                        checked={selectedNodeIds.includes(file.nodeId)}
                        onChange={(event) => toggleNodeSelection(event, file.nodeId)}
                        onClick={(event) => event.stopPropagation()}
                        aria-label={`Select file ${file.displayName || file.filename}`}
                      />
                    )}
                    <span className="file-icon">
                      {getFileIcon(file.displayName || file.filename, false, file.file_type)}
                    </span>
                    <span className="file-name">{file.displayName || file.filename}</span>
                  </span>
                  <span className="col-construct">
                    {routeState.mode === 'trash' ? (file.originalPath || file.construct_id) : (file.construct_id || '-')}
                  </span>
                  <span className="col-date">
                    {formatDate(routeState.mode === 'trash' ? file.deletedAt : (file.created_at || metadata.migrated_at))}
                  </span>
                  <span className="col-size node-actions-cell">
                    <span>{formatSize(metadata.size)}</span>
                    {routeState.mode === 'trash' ? (
                      <div className="trash-row-actions">
                        <button onClick={(event) => { event.stopPropagation(); restoreTrashItems([file]); }}>Restore</button>
                        <button className="danger" onClick={(event) => { event.stopPropagation(); permanentlyDeleteTrashItems([file]); }}>Permanent delete</button>
                      </div>
                    ) : routeState.mode === 'drive' && !file.protected && (
                      <>
                        <button className="node-more-btn" aria-label={`More actions for ${file.displayName || file.filename}`} onClick={(event) => { event.stopPropagation(); setNodeMenuId(nodeMenuId === file.nodeId ? null : file.nodeId); }}>⋮</button>
                        {nodeMenuId === file.nodeId && (
                          <div className="node-menu" role="menu">
                            <button role="menuitem" onClick={(event) => { event.stopPropagation(); setNodeMenuId(null); setRenameTarget(file); setRenameValue(file.displayName || file.filename); }}>Rename</button>
                            <button role="menuitem" onClick={(event) => { event.stopPropagation(); openMovePicker(file); }}>Move</button>
                            <button role="menuitem" onClick={(event) => { event.stopPropagation(); setNodeMenuId(null); downloadDriveFile(file).catch((error) => setUploadState({ active:false, progress:'', result:{ success:false, error:error.message } })); }}>Download</button>
                            <button role="menuitem" className="danger" onClick={(event) => { event.stopPropagation(); trashDriveNode(file); }}>Move to trash</button>
                          </div>
                        )}
                      </>
                    )}
                  </span>
                </div>
              );
            })}
            
            {routeState.mode === 'drive' && driveState.loading && (
              <div className="empty-folder"><div className="loading-spinner small"></div><p>Loading folder…</p></div>
            )}

            {routeState.mode === 'trash' && trashState.loading && (
              <div className="empty-folder"><div className="loading-spinner small"></div><p>Loading Trash…</p></div>
            )}
            {routeState.mode === 'trash' && trashState.error && (
              <div className="empty-folder drive-folder-error"><span className="empty-icon">⚠</span><p>{trashState.error}</p><button onClick={fetchTrash}>Retry</button></div>
            )}

            {routeState.mode === 'drive' && driveState.error && (
              <div className="empty-folder drive-folder-error">
                <span className="empty-icon">⚠</span>
                <p>{driveState.error}</p>
                <button onClick={() => fetchDriveChildren({ constructId: routeState.constructId, nodeId: routeState.nodeId || 'root' })}>Retry</button>
              </div>
            )}

            {!(routeState.mode === 'trash' ? trashState.loading : driveState.loading) && !(routeState.mode === 'trash' ? trashState.error : driveState.error) && folderEntries.length === 0 && fileList.length === 0 && (
              <div className="empty-folder">
                <span className="empty-icon">📭</span>
                <p>{routeState.mode === 'trash' ? 'Trash is empty' : 'This folder is empty'}</p>
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
              <button aria-label="Close preview" onClick={() => { previewRequestIdRef.current += 1; setSelectedFile(null); setFileContent(null); setMediaPreviewUrl(''); setPreviewError(null); setPreviewLoading(false); }}>×</button>
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
