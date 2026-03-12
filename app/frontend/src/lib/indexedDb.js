const DB_NAME = 'progenitor-workspace';
const DB_VERSION = 1;
const STORE_NAME = 'workspace';
const WORKSPACE_KEY = 'workspace';

function openDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);

    request.onerror = () => reject(request.error);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME);
      }
    };
    request.onsuccess = () => resolve(request.result);
  });
}

function withStore(mode, handler) {
  return openDb().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, mode);
    const store = tx.objectStore(STORE_NAME);

    tx.oncomplete = () => {
      db.close();
    };
    tx.onerror = () => {
      reject(tx.error);
      db.close();
    };

    handler(store, resolve, reject);
  }));
}

export async function loadWorkspaceState() {
  try {
    return await withStore('readonly', (store, resolve, reject) => {
      const request = store.get(WORKSPACE_KEY);
      request.onerror = () => reject(request.error);
      request.onsuccess = () => resolve(request.result || null);
    });
  } catch (error) {
    console.warn('[indexedDb] Failed to load workspace state:', error);
    return null;
  }
}

export async function saveWorkspaceState(value) {
  try {
    await withStore('readwrite', (store, resolve, reject) => {
      const request = store.put(value, WORKSPACE_KEY);
      request.onerror = () => reject(request.error);
      request.onsuccess = () => resolve();
    });
  } catch (error) {
    console.warn('[indexedDb] Failed to save workspace state:', error);
  }
}
