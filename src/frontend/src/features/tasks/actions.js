import { createAction } from '@reduxjs/toolkit';

export const kanbanSnapshotReceived = createAction('uiData/kanbanSnapshotReceived');
export const taskActivityReceived = createAction('uiData/taskActivityReceived');
export const taskContentReceived = createAction('uiData/taskContentReceived');
