import { useContext } from 'react';
import { WorkspaceContext } from '../context/WorkspaceContext';

export function useWorkspaceContext() {
  const context = useContext(WorkspaceContext);
  if (!context) {
    throw new Error('useWorkspaceContext must be used within a WorkspaceProvider');
  }
  return context;
}
