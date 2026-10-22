import { createAction } from '@reduxjs/toolkit';

export const clearActiveChatSession = createAction('uiData/clearActiveChatSession');
export const clearChatErrored = createAction('uiData/clearChatErrored');
export const clearChatGenerating = createAction('uiData/clearChatGenerating');
export const clearChatUnread = createAction('uiData/clearChatUnread');
export const markChatErrored = createAction('uiData/markChatErrored');
export const markChatGenerating = createAction('uiData/markChatGenerating');
export const markChatUnread = createAction('uiData/markChatUnread');
export const setErroredChatIds = createAction('uiData/setErroredChatIds');
export const setGeneratingChatIds = createAction('uiData/setGeneratingChatIds');
export const setUnreadChatIds = createAction('uiData/setUnreadChatIds');
export const touchChatSession = createAction('uiData/touchChatSession');
