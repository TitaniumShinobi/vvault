import React, { useState, useEffect, useCallback } from 'react';
import { authFetch, SESSION_EXPIRED_EVENT, VVAULT_DEPENDENCY_EVENT } from '../utils/authFetch';
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

const TEXT_FILE_TYPES = new Set([
  'text',
  'text/plain',
  'text/markdown',
  'conversation',
  'transcript',
  'prompt',
  'config',
  'identity',
  'capsule',
  'application/json',
]);

const PREVIEW_FETCH_TIMEOUT_MS = 2200;
const PREVIEW_BODY_HYDRATE_TIMEOUT_MS = 30000;
const DEFAULT_HOME_PATH = ['instances'];
const DEFAULT_DEGRADED_MESSAGE = 'VVAULT local dependency is temporarily unavailable. Some vault data may be missing.';

const getPreviewPath = (file) => file?.display_path || file?.storage_path || file?.filename || '';

const getDownloadFilename = (contentDisposition, fallbackName) => {
  const fallback = fallbackName || 'vvault-file';
  const encoded = (contentDisposition || '').match(/filename\*=UTF-8''([^;]+)/i);
  if (encoded?.[1]) {
    try {
      return decodeURIComponent(encoded[1]);
    } catch (err) {
      return encoded[1];
    }
  }
  const quoted = (contentDisposition || '').match(/filename="?([^";]+)"?/i);
  return quoted?.[1] || fallback;
};

const isUnexpectedAppShellDownload = (response, fallbackName) => {
  const disposition = response.headers.get('Content-Disposition') || '';
  const contentType = response.headers.get('Content-Type') || '';
  return (
    contentType.toLowerCase().includes('text/html') &&
    /filename="?index\.html"?/i.test(disposition) &&
    String(fallbackName || '').toLowerCase() !== 'index.html'
  );
};

const normalizeDegradedMessage = (message) => {
  const value = String(message || '').trim();
  if (!value) return DEFAULT_DEGRADED_MESSAGE;
  return value;
};

const isCapsuleFile = (file) => getPreviewPath(file).toLowerCase().endsWith('.capsule');

const isJsonFile = (file) => {
  const path = getPreviewPath(file).toLowerCase();
  const fileType = (file?.file_type || '').toLowerCase();
  return path.endsWith('.json') || fileType === 'application/json';
};

const looksReadableText = (value) => {
  if (typeof value !== 'string' || !value) return false;
  if (value.includes('\x00')) return false;
  const printable = Array.from(value).filter((ch) => ch === '\n' || ch === '\r' || ch === '\t' || ch >= ' ').length;
  return printable / value.length >= 0.95;
};

const isPreviewableTextFile = (file) => {
  if (!file) return false;
  if (isCapsuleFile(file)) return true;
  const fileType = (file.file_type || '').toLowerCase();
  return !fileType || TEXT_FILE_TYPES.has(fileType) || fileType.startsWith('text/');
};

const isTranscriptCandidateFile = (file) => {
  if (!file) return false;
  const path = getPreviewPath(file).toLowerCase();
  const fileType = (file?.file_type || '').toLowerCase();
  if (path.endsWith('.capsule')) return false;
  return (
    fileType === 'transcript' ||
    fileType === 'conversation' ||
    path.includes('/chatgpt/') ||
    path.includes('/chatty/') ||
    path.includes('/character.ai/') ||
    path.includes('chat_with_') ||
    path.includes('conversation') ||
    path.includes('transcript')
  );
};

const shouldLogPreviewDebug = (file, previewMeta = {}) => {
  if (!file) return false;
  return isCapsuleFile(file) || previewMeta?.preview_status === 'unavailable';
};

const logPreviewDebug = (stage, file, previewMeta = {}, preview = null) => {
  if (!shouldLogPreviewDebug(file, previewMeta)) return;

  const content = typeof previewMeta?.content === 'string' ? previewMeta.content : '';
  const path = getPreviewPath(file);
  const summary = {
    path,
    fileType: previewMeta?.file_type || file?.file_type || null,
    serverPreviewKind: previewMeta?.preview_kind || null,
    serverPreviewStatus: previewMeta?.preview_status || null,
    serverPreviewSource: previewMeta?.preview_source || null,
    serverPreviewElapsedMs: previewMeta?.preview_elapsed_ms ?? null,
    serverPreviewBudgetMs: previewMeta?.preview_budget_ms ?? null,
    serverPreviewTimedOut: Boolean(previewMeta?.preview_timed_out),
    hasContent: Boolean(content),
    contentLength: content.length,
    derivedPreviewKind: preview?.kind || null,
    derivedHasContent: Boolean(preview?.content),
  };

  console.groupCollapsed(`[VVAULT preview] ${stage} ${path}`);
  console.log('summary', summary);
  console.log('file', file);
  console.log('previewMeta', previewMeta);
  if (preview) {
    console.log('derivedPreview', preview);
  }
  if (previewMeta?.preview_status === 'unavailable' && content && preview?.kind === 'unavailable') {
    console.warn('Preview status is unavailable even though the backend returned content.');
  }
  console.groupEnd();
};

const buildPreviewState = (file, rawContent, previewMeta = {}) => {
  const content = typeof rawContent === 'string' ? rawContent : '';
  const serverPreviewKind = previewMeta?.preview_kind || null;
  const serverPreviewStatus = previewMeta?.preview_status || null;

  if (serverPreviewStatus === 'unavailable' && !content) {
    return { kind: 'unavailable', content: null };
  }
  if (!content) {
    return { kind: isPreviewableTextFile(file) ? 'unavailable' : 'binary', content: null };
  }
  if (serverPreviewKind === 'binary' && !looksReadableText(content)) {
    return { kind: 'binary', content: null };
  }
  if (serverPreviewKind === 'json' || isCapsuleFile(file) || isJsonFile(file)) {
    try {
      return { kind: 'json', content: JSON.stringify(JSON.parse(content), null, 2) };
    } catch (err) {
      // Fall through to raw text when the payload is readable but not valid JSON.
    }
  }
  return { kind: 'text', content };
};

const buildFastPreviewRequest = (file, { resolveBody = false } = {}) => ({
  id: file?.id || null,
  filename: file?.filename || file?.display_path || null,
  storage_path: file?.storage_path || file?.filename || null,
  file_type: file?.file_type || null,
  construct_id: file?.construct_id || file?.display_construct || null,
  user_id: file?.user_id || null,
  is_system: Boolean(file?.is_system),
  metadata: file?.metadata || {},
  created_at: file?.created_at || null,
  updated_at: file?.updated_at || null,
  sha256: file?.sha256 || null,
  content: typeof file?.content === 'string' ? file.content : null,
  resolve_body: resolveBody,
  candidate_transcript_ids: Array.isArray(file?.candidateTranscriptIds) ? file.candidateTranscriptIds : [],
});

const VaultBrowser = ({ user }) => {
  const [files, setFiles] = useState([]);
  const [currentPath, setCurrentPath] = useState(DEFAULT_HOME_PATH);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [selectedFile, setSelectedFile] = useState(null);
  const [fileContent, setFileContent] = useState(null);
  const [previewKind, setPreviewKind] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [viewMode, setViewMode] = useState('list');
  const [constructs, setConstructs] = useState([]);
  const [userInfo, setUserInfo] = useState({ root_label: 'Vault', is_admin: false });
  const [syncingConstruct, setSyncingConstruct] = useState(null);
  const [syncResult, setSyncResult] = useState(null);
  const [uploadState, setUploadState] = useState({ active: false, progress: '', result: null });
  const [dragOver, setDragOver] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [degraded, setDegraded] = useState({ active: false, message: '', errorCode: '' });
  const [sessionExpired, setSessionExpired] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const fileInputRef = React.useRef(null);

  const showSessionExpiredState = useCallback(() => {
    setSessionExpired(true);
    setLoading(false);
    setRefreshing(false);
    setError(null);
    setNotice('Local session expired. Sign in again after VVAULT auth storage is available.');
    setFiles([]);
    setConstructs([]);
  }, []);

  const applyDegradedContract = useCallback((data) => {
    if (!data || (data.vvault_available !== false && data.degraded !== true && data.body_database?.ready !== false)) return false;
    setDegraded({
      active: true,
      message: normalizeDegradedMessage(data.message),
      errorCode: data.error_code || 'VVAULT_DEPENDENCY_UNAVAILABLE',
    });
    return true;
  }, []);

  const clearDegradedIfHealthy = useCallback((data) => {
    if (data && (data.vvault_available === false || data.body_database?.ready === false)) return;
    setDegraded((prev) => (prev.active ? { active: false, message: '', errorCode: '' } : prev));
  }, []);

  const fetchConstructs = useCallback(async () => {
    if (sessionExpired) return false;
    try {
      const response = await authFetch('/api/chatty/constructs');
      const data = await response.json();
      if (response.status === 401 || data?.error_code === 'SESSION_EXPIRED') {
        showSessionExpiredState();
        return false;
      }
      const isDegraded = applyDegradedContract(data);
      if (!isDegraded && data.message) {
        setNotice(data.message);
      }
      if (data.success && data.constructs) {
        const formatted = data.constructs.map(c => ({
          id: c.construct_id,
          name: c.construct_id.replace(/-\d+$/, '').charAt(0).toUpperCase() + 
                c.construct_id.replace(/-\d+$/, '').slice(1),
          color: getConstructColor(c.construct_id)
        }));
        setConstructs(formatted);
        clearDegradedIfHealthy(data);
      } else if (isDegraded) {
        setConstructs(data.constructs || []);
      }
      return true;
    } catch (err) {
      console.error('Failed to fetch constructs:', err);
      return false;
    }
  }, [applyDegradedContract, clearDegradedIfHealthy, sessionExpired, showSessionExpiredState]);

  const fetchUserInfo = useCallback(async () => {
    if (sessionExpired) return false;
    try {
      const response = await authFetch('/api/vault/user-info');
      const data = await response.json();
      if (response.status === 401 || data?.error_code === 'SESSION_EXPIRED') {
        showSessionExpiredState();
        return false;
      }
      const isDegraded = applyDegradedContract(data);
      if (data.success) {
        setUserInfo({
          root_label: data.root_label || 'Vault',
          display_name: data.display_name,
          is_admin: data.is_admin || false
        });
        if (!isDegraded) {
          clearDegradedIfHealthy(data);
        }
      }
      return true;
    } catch (err) {
      console.error('Failed to fetch user info:', err);
      return false;
    }
  }, [applyDegradedContract, clearDegradedIfHealthy, sessionExpired, showSessionExpiredState]);

  const fetchFiles = useCallback(async (isRefresh = false) => {
    if (sessionExpired) return false;
    const pathSegments = Array.isArray(currentPath) ? currentPath : [];
    const pathValue = pathSegments.join('/');
    if (!isRefresh) {
      setLoading(true);
    }
    setError(null);
    try {
      if (pathSegments.length === 1 && pathSegments[0] === 'instances') {
        setFiles([]);
        setNotice(null);
        clearDegradedIfHealthy({});
        return;
      }

      const url = pathSegments.length === 0
        ? '/api/vault/files'
        : `/api/vault/files?path=${encodeURIComponent(pathValue)}`;
      const response = await authFetch(url);
      const data = await response.json();
      if (response.status === 401 || data?.error_code === 'SESSION_EXPIRED') {
        showSessionExpiredState();
        return false;
      }
      const isDegraded = applyDegradedContract(data);
      if (data.success || isDegraded) {
        setFiles(data.files || []);
        if (!isDegraded) {
          setNotice(data.message || null);
          clearDegradedIfHealthy(data);
        }
        if (data.user_root) {
          setUserInfo(prev => ({ ...prev, root_label: data.user_root }));
        }
      } else {
        setError(data.error || 'Failed to load files');
      }
      return true;
    } catch (err) {
      setError('Failed to connect to server');
      return false;
    } finally {
      if (!isRefresh) {
        setLoading(false);
      }
    }
  }, [applyDegradedContract, clearDegradedIfHealthy, currentPath, sessionExpired, showSessionExpiredState]);

  useEffect(() => {
    if (sessionExpired) return;
    let cancelled = false;
    (async () => {
      const userInfoOk = await fetchUserInfo();
      if (cancelled || !userInfoOk) return;
      await fetchFiles();
      if (cancelled) return;
      await fetchConstructs();
    })();
    return () => {
      cancelled = true;
    };
  }, [fetchUserInfo, fetchFiles, fetchConstructs, sessionExpired]);

  useEffect(() => {
    const onOutage = (event) => {
      const detail = event?.detail || {};
      setDegraded({
        active: true,
        message: normalizeDegradedMessage(detail.message),
        errorCode: detail.error_code || 'VVAULT_DEPENDENCY_UNAVAILABLE',
      });
    };
    window.addEventListener(VVAULT_DEPENDENCY_EVENT, onOutage);
    return () => window.removeEventListener(VVAULT_DEPENDENCY_EVENT, onOutage);
  }, []);

  useEffect(() => {
    const onSessionExpired = () => {
      showSessionExpiredState();
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, onSessionExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onSessionExpired);
  }, [showSessionExpiredState]);

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
      let token = null;
      try {
        const savedUser = localStorage.getItem('vvault_user');
        if (savedUser) {
          const parsed = JSON.parse(savedUser);
          if (parsed.token) token = parsed.token;
        }
      } catch (e) {}
      if (!token) token = localStorage.getItem('vvault_token');
      const response = await fetch('/api/vault/knowledge-files/upload', {
        method: 'POST',
        headers: token ? { 'Authorization': `Bearer ${token}` } : {},
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
    setSearchTerm('');
    setSelectedFile(null);
    setFileContent(null);
    setPreviewKind(null);
    setPreviewLoading(false);
  };

  const navigateBack = () => {
    setCurrentPath(currentPath.slice(0, -1));
    setSearchTerm('');
    setSelectedFile(null);
    setFileContent(null);
    setPreviewKind(null);
    setPreviewLoading(false);
  };

  const navigateHome = () => {
    setCurrentPath(DEFAULT_HOME_PATH);
    setSearchTerm('');
    setSelectedFile(null);
    setFileContent(null);
    setPreviewKind(null);
    setPreviewLoading(false);
  };

  const handleRefresh = async () => {
    if (sessionExpired) return;
    setRefreshing(true);
    setError(null);
    try {
      const userInfoOk = await fetchUserInfo();
      if (!userInfoOk) return;
      await fetchFiles(true);
      await fetchConstructs();
    } finally {
      setRefreshing(false);
    }
  };

  const navigateToBreadcrumb = (index) => {
    setCurrentPath(currentPath.slice(0, index + 1));
    setSearchTerm('');
    setSelectedFile(null);
    setFileContent(null);
    setPreviewKind(null);
    setPreviewLoading(false);
  };

  const selectFile = async (file) => {
    const previewStartedAt = performance.now();
    const candidateTranscriptIds = isCapsuleFile(file)
      ? files
          .filter((candidate) => candidate?.construct_id === file?.construct_id && isTranscriptCandidateFile(candidate))
          .sort((a, b) => {
            const aDate = Date.parse(a?.display_date || a?.created_at || 0) || 0;
            const bDate = Date.parse(b?.display_date || b?.created_at || 0) || 0;
            return bDate - aDate;
          })
          .slice(0, 6)
          .map((candidate) => candidate.id)
          .filter(Boolean)
      : [];
    const fileWithPreviewContext = candidateTranscriptIds.length
      ? { ...file, candidateTranscriptIds }
      : file;
    setSelectedFile(fileWithPreviewContext);
    setFileContent(null);
    setPreviewKind(null);
    setPreviewLoading(true);

    if (file.content && !isCapsuleFile(file) && isPreviewableTextFile(file)) {
      const preview = buildPreviewState(fileWithPreviewContext, file.content, {
        preview_kind: isJsonFile(file) ? 'json' : 'text',
        preview_status: 'inline',
      });
      setFileContent(preview.content);
      setPreviewKind(preview.kind);
      setPreviewLoading(false);
      return;
    }

    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), PREVIEW_FETCH_TIMEOUT_MS);
    console.info('[VVAULT preview] fetch-start', {
      path: getPreviewPath(file),
      fileId: file.id,
      timeoutMs: PREVIEW_FETCH_TIMEOUT_MS,
    });

    try {
      const isFastCapsulePreview = isCapsuleFile(file);
      const response = isFastCapsulePreview
        ? await authFetch('/api/vault/files/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(buildFastPreviewRequest(fileWithPreviewContext)),
            signal: controller.signal,
          })
        : await authFetch(`/api/vault/files/${file.id}`, { signal: controller.signal });
      const data = await response.json();
      const elapsedMs = Math.round(performance.now() - previewStartedAt);
      if (data.success && data.file) {
        const preview = buildPreviewState(fileWithPreviewContext, data.file.content, data.file);
        logPreviewDebug('detail-response', fileWithPreviewContext, data.file, preview);
        console.info('[VVAULT preview] fetch-complete', {
          path: getPreviewPath(fileWithPreviewContext),
          elapsedMs,
          responseStatus: response.status,
          fastPath: isFastCapsulePreview,
          previewKind: data.file.preview_kind || null,
          previewStatus: data.file.preview_status || null,
          previewSource: data.file.preview_source || null,
          previewTimedOut: Boolean(data.file.preview_timed_out),
          previewElapsedMs: data.file.preview_elapsed_ms ?? null,
        });
        setSelectedFile((current) => (current?.id === file.id ? { ...current, ...data.file } : current));
        setFileContent(preview.content);
        setPreviewKind(preview.kind);
        if (
          isFastCapsulePreview &&
          data.file.preview_status === 'unavailable' &&
          data.file.preview_source === 'fast_diagnostic'
        ) {
          const hydrateController = new AbortController();
          const hydrateTimeoutId = window.setTimeout(() => hydrateController.abort(), PREVIEW_BODY_HYDRATE_TIMEOUT_MS);
          console.info('[VVAULT preview] body-hydrate-start', {
            path: getPreviewPath(file),
            fileId: file.id,
            timeoutMs: PREVIEW_BODY_HYDRATE_TIMEOUT_MS,
          });
          void authFetch('/api/vault/files/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(buildFastPreviewRequest(fileWithPreviewContext, { resolveBody: true })),
            signal: hydrateController.signal,
          })
            .then(async (hydrateResponse) => {
              const hydrateData = await hydrateResponse.json();
              const hydrateElapsedMs = Math.round(performance.now() - previewStartedAt);
              if (hydrateData.success && hydrateData.file) {
                const hydratedPreview = buildPreviewState(fileWithPreviewContext, hydrateData.file.content, hydrateData.file);
                logPreviewDebug('body-hydrate-response', fileWithPreviewContext, hydrateData.file, hydratedPreview);
                console.info('[VVAULT preview] body-hydrate-complete', {
                  path: getPreviewPath(fileWithPreviewContext),
                  elapsedMs: hydrateElapsedMs,
                  responseStatus: hydrateResponse.status,
                  previewKind: hydrateData.file.preview_kind || null,
                  previewStatus: hydrateData.file.preview_status || null,
                  previewSource: hydrateData.file.preview_source || null,
                  previewTimedOut: Boolean(hydrateData.file.preview_timed_out),
                  previewElapsedMs: hydrateData.file.preview_elapsed_ms ?? null,
                });
                if (hydratedPreview.content) {
                  setSelectedFile((current) => (current?.id === file.id ? { ...current, ...hydrateData.file } : current));
                  setFileContent(hydratedPreview.content);
                  setPreviewKind(hydratedPreview.kind);
                }
              }
            })
            .catch((hydrateErr) => {
              const hydrateElapsedMs = Math.round(performance.now() - previewStartedAt);
              if (hydrateErr?.name === 'AbortError') {
                console.warn('[VVAULT preview] body-hydrate-timeout', {
                  path: getPreviewPath(fileWithPreviewContext),
                  elapsedMs: hydrateElapsedMs,
                  timeoutMs: PREVIEW_BODY_HYDRATE_TIMEOUT_MS,
                });
              } else {
                console.warn('[VVAULT preview] body-hydrate-failed', {
                  path: getPreviewPath(fileWithPreviewContext),
                  elapsedMs: hydrateElapsedMs,
                  error: hydrateErr?.message || String(hydrateErr),
                });
              }
            })
            .finally(() => {
              window.clearTimeout(hydrateTimeoutId);
            });
        }
      } else {
        console.warn('[VVAULT preview] fetch-failed', {
          path: getPreviewPath(fileWithPreviewContext),
          elapsedMs,
          responseStatus: response.status,
          payload: data,
        });
        logPreviewDebug('detail-failed', fileWithPreviewContext, data?.file || data || {}, { kind: 'unavailable', content: null });
        setFileContent(null);
        setPreviewKind('unavailable');
      }
    } catch (err) {
      const elapsedMs = Math.round(performance.now() - previewStartedAt);
      if (err?.name === 'AbortError') {
        console.warn('[VVAULT preview] fetch-timeout', {
          path: getPreviewPath(fileWithPreviewContext),
          elapsedMs,
          timeoutMs: PREVIEW_FETCH_TIMEOUT_MS,
        });
        setSelectedFile((current) => (current?.id === file.id ? { ...current, preview_status: 'timed_out' } : current));
      } else {
        console.error('Failed to fetch file content:', err);
      }
      setFileContent(null);
      setPreviewKind('unavailable');
    } finally {
      window.clearTimeout(timeoutId);
      setPreviewLoading(false);
    }
  };

  const downloadFile = async (file) => {
    if (!file?.id || sessionExpired) return;
    setNotice(null);
    try {
      const response = await authFetch(`/api/vault/files/${file.id}/download`);
      const fallbackName = file.displayName || file.filename || 'vvault-file';
      if (!response.ok) {
        let message = 'Download failed';
        try {
          const payload = await response.json();
          message = payload?.error || message;
        } catch (err) {
          message = response.statusText || message;
        }
        throw new Error(message);
      }
      if (isUnexpectedAppShellDownload(response, fallbackName)) {
        throw new Error('Download route is not available from the current VVAULT backend. Restart the backend and try again.');
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = getDownloadFilename(
        response.headers.get('Content-Disposition'),
        fallbackName
      );
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (err) {
      setNotice(err?.message || 'Download failed');
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
    if (!bytes && bytes !== 0) return '-';
    const kb = bytes / 1024;
    if (kb < 1024) return `${kb.toFixed(1)} KB`;
    return `${(kb / 1024).toFixed(1)} MB`;
  };

  const currentFolder = getCurrentFolder();
  const isInstancesRoot = currentPath.length === 1 && currentPath[0] === 'instances';
  const normalizedSearchTerm = searchTerm.trim().toLowerCase();
  const matchesSearch = (...values) => {
    if (!normalizedSearchTerm) return true;
    return values.some((value) => String(value || '').toLowerCase().includes(normalizedSearchTerm));
  };
  const folderEntries = (isInstancesRoot
    ? [...constructs]
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((construct) => ({
          key: `construct-${construct.id}`,
          name: construct.id,
          displayName: construct.name,
        }))
    : Object.keys(currentFolder.folders)
        .sort()
        .map((folderName) => ({
          key: `folder-${folderName}`,
          name: folderName,
          displayName: folderName,
        })))
    .filter((folderEntry) => matchesSearch(folderEntry.displayName, folderEntry.name));
  const fileList = [...currentFolder.files]
    .sort((a, b) => (a.displayName || a.filename).localeCompare(b.displayName || b.filename))
    .filter((file) => matchesSearch(
      file.displayName,
      file.filename,
      file.storage_path,
      file.display_path,
      file.internal_path,
    ));

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
          <button onClick={handleRefresh}>Retry</button>
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
                      onClick={() => { setCurrentPath(simDrivePath); setSelectedFile(null); setFileContent(null); setPreviewKind(null); setPreviewLoading(false); }}
                    >
                      ◈ SimDrive
                    </div>
                    <div
                      className={`sublink ${currentPath.join('/') === ['instances', construct.id, 'memup'].join('/') ? 'active' : ''}`}
                      onClick={() => { setCurrentPath(['instances', construct.id, 'memup']); setSelectedFile(null); setFileContent(null); setPreviewKind(null); setPreviewLoading(false); }}
                    >
                      ◈ Memup
                    </div>
                    <div
                      className={`sublink ${currentPath.join('/') === ['instances', construct.id, 'identity'].join('/') ? 'active' : ''}`}
                      onClick={() => { setCurrentPath(['instances', construct.id, 'identity']); setSelectedFile(null); setFileContent(null); setPreviewKind(null); setPreviewLoading(false); }}
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
              {syncResult.touched_files?.length ? (
                <div className="sync-files">Updated: {syncResult.touched_files.join(', ')}</div>
              ) : null}
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
            <button
              className="nav-button"
              onClick={handleRefresh}
              disabled={refreshing || loading}
              title="Refresh vault"
            >
              {refreshing ? <span className="upload-spinner" /> : '↻'}
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
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
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

        {degraded.active && (
          <div className="vault-notice vault-notice-outage">
            <span className="vault-notice-icon">!</span>
            <span>{degraded.message}</span>
            <button
              type="button"
              className="vault-notice-retry"
              onClick={handleRefresh}
              disabled={refreshing || loading}
            >
              {refreshing ? 'Retrying...' : 'Retry'}
            </button>
          </div>
        )}

        {notice && !degraded.active && (
          <div className="vault-notice">
            <span className="vault-notice-icon">!</span>
            <span>{notice}</span>
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
              <span className="col-date">DATE MODIFIED</span>
              <span className="col-size">SIZE</span>
            </div>
            
            {folderEntries.map((folderEntry, idx) => (
              <div 
                key={folderEntry.key || `folder-${idx}`}
                className="file-row folder"
                onDoubleClick={() => navigateToFolder(folderEntry.name)}
              >
                <span className="col-name">
                  <span className="file-icon">{getFileIcon(folderEntry.displayName, true)}</span>
                  <span className="file-name">{folderEntry.displayName}</span>
                </span>
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
                <span className="col-date">
                  {formatDate(file.display_date || file.created_at || metadata.migrated_at)}
                </span>
                <span className="col-size">
                  {formatSize(file.display_size || metadata.size)}
                </span>
              </div>
            );
          })}

            {folderEntries.length === 0 && fileList.length === 0 && (
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
              <button type="button" onClick={() => downloadFile(selectedFile)}>Download</button>
              <button type="button" onClick={() => setSelectedFile(null)}>×</button>
            </div>
            <div className="preview-content">
              {previewLoading ? (
                <div className="binary-preview">
                  <span className="binary-icon">…</span>
                  <p>Loading preview...</p>
                </div>
              ) : previewKind === 'unavailable' ? (
                <div className="binary-preview">
                  <span className="binary-icon">📎</span>
                  <p>{`Preview unavailable - ${selectedFile.filename}`}</p>
                  <p className="binary-info">
                    {selectedFile?.preview_status === 'unavailable'
                      ? 'Content could not be recovered from storage.'
                      : 'Preview content is not currently available.'}
                  </p>
                </div>
              ) : previewKind === 'binary' ? (
                <div className="binary-preview">
                  <span className="binary-icon">📎</span>
                  <p>{`Binary file - ${selectedFile.filename}`}</p>
                  <p className="binary-info">
                    {'Stored in cloud storage'}
                  </p>
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
