import { WORKSPACE_LOCAL_STORAGE_KEYS } from '../lib/browserContracts';

// Browser-origin caches hold data scoped to the active backend. localStorage
// is frontend-origin scoped, so clear the registry's workspace-local keys on
// an in-place workspace switch while preserving global preferences.

/** Clears every localStorage cache scoped to the previously-active workspace. */
export function clearWorkspaceScopedCaches() {
  WORKSPACE_LOCAL_STORAGE_KEYS.forEach((key) => localStorage.removeItem(key));
}
