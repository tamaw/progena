import { useCallback } from 'react';
import { useDispatch } from 'react-redux';
import { useConfig } from '../../context/ConfigContext';
import {
  createTaskActivity as createActivity,
  deleteKanbanCard as deleteCard,
  updateKanbanCard as updateCard,
} from './state';

/** Bind feature operations to the active workspace and Redux request lifecycle. */
export function useTasksApi() {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const updateKanbanCard = useCallback(
    (args) => dispatch(updateCard({ ...args, kanbanUrl: getApiUrl('kanban') })).unwrap(),
    [dispatch, getApiUrl],
  );
  const deleteKanbanCard = useCallback(
    (args) => dispatch(deleteCard({ ...args, kanbanUrl: getApiUrl('kanban') })).unwrap(),
    [dispatch, getApiUrl],
  );
  const createTaskActivity = useCallback(
    (args) => dispatch(createActivity({ ...args, tasksUrl: getApiUrl('/api/ui/tasks') })).unwrap(),
    [dispatch, getApiUrl],
  );
  return { updateKanbanCard, deleteKanbanCard, createTaskActivity };
}
