import React, { useState, useRef } from 'react';
import { authFetch } from '../utils/authFetch';

const inputStyle = {
  background: '#1a1a1a',
  color: '#e0e0e0',
  border: '1px solid #333',
  padding: '10px 12px',
  borderRadius: '6px',
  width: '100%',
  boxSizing: 'border-box',
  fontSize: '14px',
  fontFamily: 'monospace'
};

const textareaStyle = {
  ...inputStyle,
  minHeight: '100px',
  resize: 'vertical'
};

const labelStyle = {
  display: 'block',
  marginBottom: '6px',
  color: '#aaa',
  fontSize: '13px',
  fontFamily: 'monospace',
  textTransform: 'uppercase',
  letterSpacing: '0.5px'
};

const fieldGroupStyle = {
  marginBottom: '20px'
};

const CreateConstruct = ({ user }) => {
  const [name, setName] = useState('');
  const [callsign, setCallsign] = useState('');
  const [callsignManual, setCallsignManual] = useState(false);
  const [description, setDescription] = useState('');
  const [instructions, setInstructions] = useState('');
  const [starters, setStarters] = useState([]);
  const [starterInput, setStarterInput] = useState('');
  const [colorHex, setColorHex] = useState('#722F37');
  const [centerImage, setCenterImage] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const fileInputRef = useRef(null);

  const handleNameChange = (e) => {
    const val = e.target.value;
    setName(val);
    if (!callsignManual) {
      const slug = val.toLowerCase().replace(/[^a-z0-9]/g, '');
      setCallsign(slug ? `${slug}-001` : '');
    }
  };

  const handleCallsignChange = (e) => {
    setCallsignManual(true);
    setCallsign(e.target.value);
  };

  const validateCallsign = (cs) => {
    return /^[a-z0-9]+-\d{3}$/.test(cs);
  };

  const addStarter = () => {
    const trimmed = starterInput.trim();
    if (trimmed && !starters.includes(trimmed)) {
      setStarters([...starters, trimmed]);
      setStarterInput('');
    }
  };

  const removeStarter = (index) => {
    setStarters(starters.filter((_, i) => i !== index));
  };

  const handleStarterKeyDown = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      addStarter();
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(null);
    setResult(null);

    if (!name.trim()) {
      setError('Name is required.');
      return;
    }
    if (!callsign.trim() || !validateCallsign(callsign.trim())) {
      setError('Callsign must match pattern {name}-{NNN} (e.g. sera-001).');
      return;
    }

    setLoading(true);

    try {
      let token = null;
      try {
        const savedUser = localStorage.getItem('vvault_user');
        if (savedUser) {
          const parsed = JSON.parse(savedUser);
          if (parsed.token) token = parsed.token;
        }
      } catch (err) {}
      if (!token) token = localStorage.getItem('vvault_token') || null;

      const formData = new FormData();
      formData.append('name', name.trim());
      formData.append('callsign', callsign.trim());
      if (description.trim()) formData.append('description', description.trim());
      if (instructions.trim()) formData.append('instructions', instructions.trim());
      if (starters.length > 0) formData.append('conversationStarters', JSON.stringify(starters));
      formData.append('color_hex', colorHex);
      if (centerImage) formData.append('center_image', centerImage);

      const headers = {};
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }

      const response = await fetch('/api/chatty/construct/create', {
        method: 'POST',
        headers,
        body: formData
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || data.message || `Server error ${response.status}`);
      }

      setResult(data);
    } catch (err) {
      setError(err.message || 'Failed to create construct.');
    } finally {
      setLoading(false);
    }
  };

  const resetForm = () => {
    setName('');
    setCallsign('');
    setCallsignManual(false);
    setDescription('');
    setInstructions('');
    setStarters([]);
    setStarterInput('');
    setColorHex('#722F37');
    setCenterImage(null);
    setError(null);
    setResult(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  return (
    <div className="create-construct">
      <div className="page-header">
        <h1 className="page-title">🛠️ Create Construct</h1>
        <p className="page-subtitle">Initialize a new AI construct in the VVAULT system</p>
      </div>

      {result ? (
        <div style={{ marginTop: '20px' }}>
          <div className="card">
            <div className="card-header">
              <h3 className="card-title">✅ Construct Created</h3>
            </div>
            <div className="stat-value" style={{ color: '#4caf50', fontSize: '18px', marginBottom: '8px' }}>
              {result.callsign || callsign} initialized successfully
            </div>
            <div className="stat-label">New construct is ready for activation</div>
          </div>

          {result.file_count !== undefined && (
            <div className="card" style={{ marginTop: '16px' }}>
              <div className="card-header">
                <h3 className="card-title">📁 Files Created</h3>
              </div>
              <div className="stat-value">{result.file_count}</div>
              <div className="stat-label">files generated</div>
            </div>
          )}

          {(result.directory_template || result.directory_structure) && (
            <div className="card" style={{ marginTop: '16px' }}>
              <div className="card-header">
                <h3 className="card-title">🗂️ Directory Structure</h3>
              </div>
              {(() => {
                const dirData = result.directory_template || result.directory_structure;
                if (typeof dirData === 'object' && dirData !== null) {
                  return (
                    <div style={{ marginTop: '10px' }}>
                      {Object.entries(dirData).map(([folder, files]) => (
                        <div key={folder} style={{ marginBottom: '12px' }}>
                          <div style={{ color: '#aaa', fontFamily: 'monospace', fontSize: '13px', marginBottom: '4px' }}>
                            {folder}/
                          </div>
                          {Array.isArray(files) && files.length > 0 ? (
                            files.map((f, i) => (
                              <div key={i} style={{ color: '#e0e0e0', fontFamily: 'monospace', fontSize: '12px', paddingLeft: '20px' }}>
                                {f}
                              </div>
                            ))
                          ) : (
                            <div style={{ color: '#555', fontFamily: 'monospace', fontSize: '12px', paddingLeft: '20px' }}>
                              (empty)
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  );
                }
                return (
                  <pre style={{ ...inputStyle, whiteSpace: 'pre-wrap', marginTop: '10px' }}>
                    {JSON.stringify(dirData, null, 2)}
                  </pre>
                );
              })()}
            </div>
          )}

          {result.glyph && (
            <div className="card" style={{ marginTop: '16px' }}>
              <div className="card-header">
                <h3 className="card-title">🔮 Glyph Info</h3>
              </div>
              {result.glyph.number_rows && (
                <div style={{ marginTop: '10px' }}>
                  <div className="stat-label" style={{ marginBottom: '8px' }}>Number Rows</div>
                  {result.glyph.number_rows.map((row, i) => (
                    <div key={i} style={{
                      fontFamily: 'monospace',
                      color: '#e0e0e0',
                      padding: '4px 0',
                      fontSize: '14px',
                      letterSpacing: '2px'
                    }}>
                      {Array.isArray(row) ? row.join('  ') : String(row)}
                    </div>
                  ))}
                </div>
              )}
              {result.glyph.color && (
                <div style={{ marginTop: '12px', display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <div style={{
                    width: '24px',
                    height: '24px',
                    borderRadius: '4px',
                    background: result.glyph.color,
                    border: '1px solid #555'
                  }}></div>
                  <span className="stat-label">{result.glyph.color}</span>
                </div>
              )}
              {result.glyph.path && (
                <div style={{ marginTop: '8px' }}>
                  <span className="stat-label">Path: {result.glyph.path}</span>
                </div>
              )}
            </div>
          )}

          <div style={{ marginTop: '20px' }}>
            <button
              className="btn btn-primary"
              onClick={resetForm}
              style={{ marginRight: '12px' }}
            >
              ➕ Create Another
            </button>
          </div>
        </div>
      ) : (
        <form onSubmit={handleSubmit} style={{ marginTop: '20px' }}>
          <div className="card">
            <div className="card-header">
              <h3 className="card-title">🧬 Construct Identity</h3>
            </div>

            <div style={fieldGroupStyle}>
              <label style={labelStyle}>Name *</label>
              <input
                type="text"
                value={name}
                onChange={handleNameChange}
                placeholder="e.g. Sera"
                style={inputStyle}
                required
              />
            </div>

            <div style={fieldGroupStyle}>
              <label style={labelStyle}>Callsign *</label>
              <input
                type="text"
                value={callsign}
                onChange={handleCallsignChange}
                placeholder="e.g. sera-001"
                style={{
                  ...inputStyle,
                  borderColor: callsign && !validateCallsign(callsign) ? '#f44336' : '#333'
                }}
                required
              />
              {callsign && !validateCallsign(callsign) && (
                <div style={{ color: '#f44336', fontSize: '12px', marginTop: '4px', fontFamily: 'monospace' }}>
                  Must match pattern: name-NNN (e.g. sera-001)
                </div>
              )}
            </div>

            <div style={fieldGroupStyle}>
              <label style={labelStyle}>Description</label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Brief description of the construct's purpose..."
                style={textareaStyle}
              />
            </div>

            <div style={fieldGroupStyle}>
              <label style={labelStyle}>Instructions (System Prompt)</label>
              <textarea
                value={instructions}
                onChange={(e) => setInstructions(e.target.value)}
                placeholder="System prompt body for the construct..."
                style={{ ...textareaStyle, minHeight: '150px' }}
              />
            </div>
          </div>

          <div className="card" style={{ marginTop: '16px' }}>
            <div className="card-header">
              <h3 className="card-title">💬 Conversation Starters</h3>
            </div>

            <div style={{ display: 'flex', gap: '8px', marginBottom: '12px' }}>
              <input
                type="text"
                value={starterInput}
                onChange={(e) => setStarterInput(e.target.value)}
                onKeyDown={handleStarterKeyDown}
                placeholder="Add a conversation starter..."
                style={{ ...inputStyle, flex: 1 }}
              />
              <button
                type="button"
                className="btn btn-secondary"
                onClick={addStarter}
                style={{ whiteSpace: 'nowrap' }}
              >
                + Add
              </button>
            </div>

            {starters.length > 0 ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {starters.map((starter, index) => (
                  <div key={index} style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    background: '#111',
                    border: '1px solid #333',
                    borderRadius: '4px',
                    padding: '8px 12px',
                    fontFamily: 'monospace',
                    fontSize: '13px',
                    color: '#e0e0e0'
                  }}>
                    <span>{starter}</span>
                    <button
                      type="button"
                      onClick={() => removeStarter(index)}
                      style={{
                        background: 'transparent',
                        border: 'none',
                        color: '#f44336',
                        cursor: 'pointer',
                        fontSize: '16px',
                        padding: '0 4px'
                      }}
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <div className="stat-label">No starters added yet</div>
            )}
          </div>

          <div className="card" style={{ marginTop: '16px' }}>
            <div className="card-header">
              <h3 className="card-title">🔮 Glyph Configuration</h3>
            </div>

            <div style={fieldGroupStyle}>
              <label style={labelStyle}>Color Hex</label>
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <input
                  type="color"
                  value={colorHex}
                  onChange={(e) => setColorHex(e.target.value)}
                  style={{
                    width: '48px',
                    height: '36px',
                    padding: '2px',
                    background: '#1a1a1a',
                    border: '1px solid #333',
                    borderRadius: '4px',
                    cursor: 'pointer'
                  }}
                />
                <input
                  type="text"
                  value={colorHex}
                  onChange={(e) => setColorHex(e.target.value)}
                  style={{ ...inputStyle, width: '140px' }}
                  placeholder="#722F37"
                />
                <div style={{
                  width: '24px',
                  height: '24px',
                  borderRadius: '50%',
                  background: colorHex,
                  border: '1px solid #555'
                }}></div>
              </div>
            </div>

            <div style={fieldGroupStyle}>
              <label style={labelStyle}>Center Image</label>
              <input
                type="file"
                ref={fileInputRef}
                accept=".png,.jpg,.jpeg,.svg"
                onChange={(e) => setCenterImage(e.target.files[0] || null)}
                style={{
                  ...inputStyle,
                  padding: '8px',
                  cursor: 'pointer'
                }}
              />
              <div style={{ color: '#666', fontSize: '12px', marginTop: '4px', fontFamily: 'monospace' }}>
                Accepts PNG, JPG, or SVG. Used as glyph center image.
              </div>
            </div>
          </div>

          {error && (
            <div className="card" style={{ marginTop: '16px', borderColor: '#f44336' }}>
              <div style={{ color: '#f44336', fontFamily: 'monospace', fontSize: '14px', padding: '4px 0' }}>
                ⚠️ {error}
              </div>
            </div>
          )}

          <div style={{ marginTop: '20px' }}>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={loading || !name.trim() || !callsign.trim() || !validateCallsign(callsign)}
              style={{
                opacity: loading ? 0.6 : 1,
                cursor: loading ? 'not-allowed' : 'pointer',
                minWidth: '200px'
              }}
            >
              {loading ? (
                <>
                  <span className="spinner" style={{ marginRight: '8px' }}></span>
                  Creating...
                </>
              ) : (
                '🛠️ Create Construct'
              )}
            </button>
          </div>
        </form>
      )}
    </div>
  );
};

export default CreateConstruct;
