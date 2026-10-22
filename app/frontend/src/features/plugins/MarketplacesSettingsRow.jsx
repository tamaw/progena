import { useCallback, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { selectMarketplaces, fetchMarketplaces, saveMarketplaces } from './state';
import { useConfig } from '../../context/ConfigContext';

export function MarketplacesSettingsRow() {
  const { getApiUrl } = useConfig();
  const dispatch = useDispatch();
  const marketplacesUrl = getApiUrl('marketplaces');
  const marketplaces = useSelector(selectMarketplaces);
  const [newName, setNewName] = useState('');
  const [newSource, setNewSource] = useState('');
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);
  const [pendingName, setPendingName] = useState(null);

  if (pendingName && marketplaces[pendingName]?.source === newSource.trim()) {
    setCreating(false);
    setPendingName(null);
    setNewName('');
    setNewSource('');
  }

  const handleRefresh = useCallback(async () => {
    setError('');
    try {
      await dispatch(fetchMarketplaces({ marketplacesUrl })).unwrap();
    } catch (err) {
      setError(err?.message || 'Failed to refresh marketplaces');
    }
  }, [dispatch, marketplacesUrl]);

  const handleSave = useCallback(async (nextMarketplaces) => {
    setError('');
    try {
      await dispatch(saveMarketplaces({ marketplacesUrl, marketplaces: nextMarketplaces })).unwrap();
    } catch (err) {
      setError(err?.message || 'Failed to save marketplaces');
      setPendingName(null);
    }
  }, [dispatch, marketplacesUrl]);

  const handleRemove = (name) => {
    const next = { ...marketplaces };
    delete next[name];
    void handleSave(next);
  };

  const handleAdd = () => {
    const name = newName.trim();
    const source = newSource.trim();
    if (!name || !source) {
      setError('Name and source URL are required');
      return;
    }
    if (Object.prototype.hasOwnProperty.call(marketplaces, name)) {
      setError(`Marketplace "${name}" already exists`);
      return;
    }
    const next = { ...marketplaces, [name]: { source, added_at: new Date().toISOString() } };
    setPendingName(name);
    void handleSave(next);
  };

  const cancelCreate = () => {
    setCreating(false);
    setNewName('');
    setNewSource('');
    setError('');
  };

  if (creating) {
    return (
      <section className="set-section is-active">
        <button type="button" className="set-back" disabled={!!pendingName} onClick={cancelCreate}>← Marketplaces</button>
        <header className="set-section__head">
          <h3>Add marketplace</h3>
          <p>Register a source to browse and install plugins.</p>
        </header>
        <form onSubmit={(event) => { event.preventDefault(); handleAdd(); }}>
          <label className="set-row set-connection-field">
            <span className="set-row__title">Name</span>
            <input className="set-text-input" value={newName} placeholder="my-marketplace" disabled={!!pendingName} onChange={(event) => { setNewName(event.target.value); setError(''); }} />
          </label>
          <label className="set-row set-connection-field">
            <span className="set-row__title">Source</span>
            <input className="set-text-input" value={newSource} placeholder="https://github.com/owner/repo" disabled={!!pendingName} onChange={(event) => { setNewSource(event.target.value); setError(''); }} />
          </label>
          {error ? <p className="set-workspace-list__error" role="alert">{error}</p> : null}
          <div className="set-editor-footer">
            <button type="button" className="task-btn task-btn--secondary" disabled={!!pendingName} onClick={cancelCreate}>Cancel</button>
            <button type="submit" className="task-btn task-btn--primary" disabled={!!pendingName}>{pendingName ? 'Adding…' : 'Add marketplace'}</button>
          </div>
        </form>
      </section>
    );
  }
  return (
    <section className="set-section is-active">
      <header className="set-section__head">
        <h3>Marketplaces</h3>
        <p>Registered plugin marketplace sources.</p>
      </header>

      <ul className="set-connection-list">
          {Object.entries(marketplaces).map(([name, entry]) => (
            <li key={name} className="set-row">
              <div className="set-row__label">
                <span className="set-row__title">{name}</span>
                <span className="set-row__sub">{entry?.source}</span>
              </div>
              <button type="button" className="task-btn task-btn--secondary set-btn--danger" onClick={() => handleRemove(name)}>
                Remove
              </button>
            </li>
          ))}
      </ul>
      <div className="set-row">
        <div className="set-row__label">
          <span className="set-row__title">Add a marketplace</span>
          <span className="set-row__sub">Connect a plugin source using its repository address.</span>
        </div>
        <button type="button" className="task-btn task-btn--secondary" onClick={() => { setCreating(true); setError(''); }}>Add marketplace</button>
      </div>
      <div className="set-row">
        <div className="set-row__label">
          <span className="set-row__title">Refresh sources</span>
          <span className="set-row__sub">Reload the registered marketplace list.</span>
        </div>
        <button type="button" className="task-btn task-btn--secondary" onClick={handleRefresh}>Refresh</button>
      </div>
      {error ? <p className="set-workspace-list__error" role="alert">{error}</p> : null}
    </section>
  );
}
