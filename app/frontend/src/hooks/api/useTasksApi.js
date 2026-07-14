import { useCallback } from 'react';
import { useConfig } from '../../context/ConfigContext';
import { requestAcknowledged } from '../../store/http';

/** Kanban mutations and activity creation acknowledge immediately; state arrives on /api/ui/stream. */
export function useTasksApi() {
  const { getApiUrl } = useConfig();

  const updateKanbanCard = useCallback(
    ({ cardId, payload }) => requestAcknowledged(
      `${getApiUrl('kanban')}/cards/${encodeURIComponent(cardId)}`,
      { method: 'PATCH', body: payload, requestId: crypto.randomUUID() },
    ),
    [getApiUrl],
  );

  const deleteKanbanCard = useCallback(
    ({ cardId }) => requestAcknowledged(
      `${getApiUrl('kanban')}/cards/${encodeURIComponent(cardId)}`,
      { method: 'DELETE', requestId: crypto.randomUUID() },
    ),
    [getApiUrl],
  );

  const createTaskActivity = useCallback(
    ({ cardId, payload }) => requestAcknowledged(
      getApiUrl(`/api/ui/tasks/${encodeURIComponent(cardId)}/activity`),
      { method: 'POST', body: payload, requestId: crypto.randomUUID() },
    ),
    [getApiUrl],
  );

  return { updateKanbanCard, deleteKanbanCard, createTaskActivity };
}
