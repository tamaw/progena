import { useCallback } from 'react';
import { useConfig } from '../../context/ConfigContext';
import { createOperationRequestId, requestAcknowledged } from '../../store/http';
import { createUuid } from '../../lib/uuid';

/** Kanban mutations and activity creation acknowledge immediately; state arrives on /api/ui/stream. */
export function useTasksApi() {
  const { getApiUrl } = useConfig();

  // Draft saves pass their own requestId so the save is correlated (and
  // failed by a disconnect) before the HTTP acknowledgement arrives.
  const updateKanbanCard = useCallback(
    ({ cardId, payload, requestId }) => requestAcknowledged(
      `${getApiUrl('kanban')}/cards/${encodeURIComponent(cardId)}`,
      { method: 'PATCH', body: payload, requestId: createOperationRequestId({ requestId }) },
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
