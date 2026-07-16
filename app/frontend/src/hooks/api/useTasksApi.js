import { useCallback } from 'react';
import { useConfig } from '../../context/ConfigContext';
import { requestAcknowledged } from '../../store/http';
import { createUuid } from '../../lib/uuid';

/** Kanban mutations and activity creation acknowledge immediately; state arrives on /api/ui/stream. */
export function useTasksApi() {
  const { getApiUrl } = useConfig();

  const updateKanbanCard = useCallback(
    ({ cardId, payload }) => requestAcknowledged(
      `${getApiUrl('kanban')}/cards/${encodeURIComponent(cardId)}`,
      { method: 'PATCH', body: payload, requestId: createUuid() },
    ),
    [getApiUrl],
  );

  const deleteKanbanCard = useCallback(
    ({ cardId }) => requestAcknowledged(
      `${getApiUrl('kanban')}/cards/${encodeURIComponent(cardId)}`,
      { method: 'DELETE', requestId: createUuid() },
    ),
    [getApiUrl],
  );

  const createTaskActivity = useCallback(
    ({ cardId, payload }) => requestAcknowledged(
      getApiUrl(`/api/ui/tasks/${encodeURIComponent(cardId)}/activity`),
      { method: 'POST', body: payload, requestId: createUuid() },
    ),
    [getApiUrl],
  );

  return { updateKanbanCard, deleteKanbanCard, createTaskActivity };
}
