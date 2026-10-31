import { useCallback, useEffect, useRef, useState } from 'react';
import { useConfig } from '../../../context/ConfigContext';
import { useNotifications } from '../../../context/NotificationContext';
import { reportFailure } from '../../../lib/failures';
import { createUuid } from '../../../lib/uuid';
import { subscribeUiEvent } from '../../../realtime/realtimeEvents';
import { UI_EVENT } from '../../../realtime/eventVocabulary';
import { requestAcknowledged } from '../../../store/http';

function normalizeSnapshot(payload, conversationId) {
  if (
    !payload
    || payload.conversation_id !== conversationId
    || !Array.isArray(payload.messages)
    || !Number.isInteger(payload.revision)
    || payload.revision < 0
  ) {
    return null;
  }
  return {
    conversationId,
    revision: payload.revision,
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

/** Queue mutations complete through correlated events, not the HTTP acknowledgement. */
export function useChatQueue(conversationId) {
  const { getApiUrl } = useConfig();
  const { show } = useNotifications();
  const [snapshot, setSnapshot] = useState(null);
  const [pendingMutation, setPendingMutation] = useState(null);
  const revisionRef = useRef(-1);
  const commandRef = useRef(null);
  const activeSnapshot = snapshot?.conversationId === conversationId ? snapshot : null;
  const isMutating = pendingMutation?.conversationId === conversationId;

  const requestSnapshot = useCallback(async (report = false) => {
    if (!conversationId) return false;
    try {
      await requestAcknowledged(queueUrl(getApiUrl, conversationId), { requestId: createUuid() });
      return true;
    } catch (error) {
      if (report) reportFailure(show, 'Load message queue', error);
      return false;
    }
  }, [conversationId, getApiUrl, show]);

  useEffect(() => {
    commandRef.current = null;
    revisionRef.current = -1;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- a queue command belongs only to its originating conversation.
    setPendingMutation(null);
    if (!conversationId) return undefined;

    const unsubscribe = subscribeUiEvent((event) => {
      if (event?.type === UI_EVENT.CONNECTION_READY) {
        commandRef.current = null;
        setPendingMutation((current) => (
          current?.conversationId === conversationId ? null : current
        ));
        void requestSnapshot(true);
        return;
      }
      if (event?.type !== UI_EVENT.CHAT_QUEUE_UPDATED) return;
      const next = normalizeSnapshot(event.payload, conversationId);
      if (!next) return;
      if (next.revision > revisionRef.current) {
        revisionRef.current = next.revision;
        setSnapshot(next);
      }
      if (event.payload?.request_id === commandRef.current) {
        commandRef.current = null;
        setPendingMutation(null);
      }
    });
    void requestSnapshot(true);
    return unsubscribe;
  }, [conversationId, requestSnapshot]);

  const dispatchAction = useCallback(async ({ action, messageId, order }) => {
    if (!conversationId || !activeSnapshot || isMutating || commandRef.current) return false;
    const commandId = createUuid();
    commandRef.current = commandId;
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
    } catch (error) {
      if (commandRef.current === commandId) {
        commandRef.current = null;
        setPendingMutation(null);
      }
      reportFailure(show, 'Update message queue', error);
      void requestSnapshot(false);
      return false;
    }
  }, [activeSnapshot, conversationId, getApiUrl, isMutating, requestSnapshot, show]);

  return {
    snapshot: activeSnapshot,
    isMutating,
    deleteMessage: (messageId) => dispatchAction({ action: 'delete', messageId }),
    reorderMessages: (order) => dispatchAction({ action: 'reorder', order }),
    sendNow: (messageId) => dispatchAction({ action: 'send_now', messageId }),
  };
}
