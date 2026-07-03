import { useCallback } from 'react';
import { useApiClient } from './useApiClient';

/** Kanban card mutations + task activity creation (Tasks page). */
export function useTasksApi() {
  const { request } = useApiClient();

  const updateKanbanCard = useCallback(
    (cardId, payload) => request(`/api/ui/kanban/cards/${cardId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
    [request],
  );

  const deleteKanbanCard = useCallback(
    (cardId) => request(`/api/ui/kanban/cards/${cardId}`, { method: 'DELETE' }),
    [request],
  );

  const createTaskActivity = useCallback(
    (cardId, payload) => request(`/api/ui/tasks/${cardId}/activity`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
    [request],
  );

  return { updateKanbanCard, deleteKanbanCard, createTaskActivity };
}
