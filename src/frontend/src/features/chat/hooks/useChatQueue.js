import { useCallback, useEffect, useRef, useState } from 'react';
import { useConfig } from '../../../context/ConfigContext';
import { createUuid } from '../../../lib/uuid';
import { subscribeUiEvent } from '../../../realtime/realtimeEvents';
import { UI_EVENT } from '../../../realtime/eventVocabulary';
import { requestAcknowledged } from '../../../store/http';

function normalizeSnapshot(payload, conversationId) {
  if (!payload || payload.conversation_id !== conversationId || !Array.isArray(payload.messages)) {
    return null;
  }
  const revision = Number.isInteger(payload.revision) && payload.revision >= 0 ? payload.revision : null;
  if (revision === null) {
    return null;
  }
  return {
    conversationId,
    revision,
    messages: payload.messages.filter((message) => (
      typeof message?.id === 'string'
      && typeof message?.text === 'string'
      && (message.status === 'queued' || message.status === 'sending')
    )),
  };
}

function queueUrl(getApiUrl, conversationId) {
  return `${getApiUrl('/api/chat/sessions').replace(/\/$/, '')}/${encodeURIComponent(conversationId)}/queue`;
}

function requestQueueSnapshot(getApiUrl, conversationId) {
  return requestAcknowledged(queueUrl(getApiUrl, conversationId), { requestId: createUuid() }).catch(() => {
    // The existing canonical event stream surfaces transport outages. Do not
    // manufacture a local queue snapshot when recovery is unavailable.
  });
}

/** Authoritative per-conversation queue hydrated and mutated exclusively via SSE. */
export function useChatQueue(conversationId) {
  const { getApiUrl } = useConfig();
  const [snapshot, setSnapshot] = useState(null);
  const [pendingMutation, setPendingMutation] = useState(null);
  const pendingCommandIdRef = useRef(null);
  const revisionRef = useRef(-1);
  const activeSnapshot = snapshot?.conversationId === conversationId ? snapshot : null;
  const isMutating = pendingMutation?.conversationId === conversationId;

  useEffect(() => {
    pendingCommandIdRef.current = null;
    revisionRef.current = -1;
    if (!conversationId) {
      return undefined;
    }

    const unsubscribe = subscribeUiEvent((event) => {
      if (event?.type === UI_EVENT.CONNECTION_READY) {
        // A reconnect can have missed a bounded replay window. The server
        // snapshot is authoritative and supersedes any locally pending action.
        pendingCommandIdRef.current = null;
        setPendingMutation((current) => (
          current?.conversationId === conversationId ? null : current
        ));
        void requestQueueSnapshot(getApiUrl, conversationId);
        return;
      }
      if (event?.type !== UI_EVENT.CHAT_QUEUE_UPDATED) {
        return;
      }
      const next = normalizeSnapshot(event.payload, conversationId);
      if (!next) {
        return;
      }
      const completesPendingCommand = event.payload?.request_id === pendingCommandIdRef.current;
      if (next.revision > revisionRef.current) {
        revisionRef.current = next.revision;
        setSnapshot(next);
      }
      if (completesPendingCommand) {
        pendingCommandIdRef.current = null;
        setPendingMutation((current) => (
          current?.commandId === event.payload?.request_id ? null : current
        ));
      }
    });
    void requestQueueSnapshot(getApiUrl, conversationId);
    return unsubscribe;
  }, [conversationId, getApiUrl]);

  const dispatchAction = useCallback(async ({ action, messageId, order }) => {
    if (!conversationId || activeSnapshot === null || isMutating) {
      return false;
    }
    const commandId = createUuid();
    pendingCommandIdRef.current = commandId;
    setPendingMutation({ conversationId, commandId });
    try {
      await requestAcknowledged(`${queueUrl(getApiUrl, conversationId)}/actions`, {
        method: 'POST',
        requestId: commandId,
        body: {
          action,
          ...(messageId ? { message_id: messageId } : {}),
          ...(order ? { order } : {}),
          revision: activeSnapshot.revision,
          command_id: commandId,
        },
      });
      return true;
    } catch {
      if (pendingCommandIdRef.current === commandId) {
        pendingCommandIdRef.current = null;
        setPendingMutation((current) => (
          current?.commandId === commandId ? null : current
        ));
      }
      void requestQueueSnapshot(getApiUrl, conversationId);
      return false;
    }
  }, [activeSnapshot, conversationId, getApiUrl, isMutating]);

  return {
    isMutating,
    snapshot: activeSnapshot,
    deleteMessage: (messageId) => dispatchAction({ action: 'delete', messageId }),
    reorderMessages: (order) => dispatchAction({ action: 'reorder', order }),
    sendNow: (messageId) => dispatchAction({ action: 'send_now', messageId }),
  };
}
