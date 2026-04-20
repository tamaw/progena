import { useState, useEffect, useRef, useCallback } from 'react';
import { useApi } from '../hooks/useApi';
import { useEventsSSE } from '../hooks/useSSE';
import { useConfig } from '../context/ConfigContext';
import { useNotifications } from '../context/NotificationContext';
import Header from '../components/layout/Header';
import ChatInput from '../components/chat/ChatInput';
import Message from '../components/chat/Message';
import TypingIndicator from '../components/chat/TypingIndicator';
import QuickActions from '../components/chat/QuickActions';
import StreamFilterMenu from '../components/chat/StreamFilterMenu';
import {
  PERSISTED_CATEGORIES,
  categoryForEventType,
  isEventVisible,
  loadStreamFilters,
} from '../components/chat/streamFilters';

const MAX_STREAM_EVENTS_PER_MESSAGE = 200;
const MAX_TOKEN_DELTAS_PER_MESSAGE = 500;
const CANCELLED_SUFFIX = ' [Cancelled]';

function normalizeStreamEvent(event, index) {
  if (!event || typeof event !== 'object') {
    return null;
  }

  return {
    id: typeof event.id === 'string' ? event.id : `evt_restored_${index}_${Date.now()}`,
    eventType: typeof event.eventType === 'string'
      ? event.eventType
      : (typeof event.event_type === 'string' ? event.event_type : 'stream_event'),
    content: typeof event.content === 'string' ? event.content : '',
    metadata: event.metadata && typeof event.metadata === 'object' && !Array.isArray(event.metadata)
      ? event.metadata
      : {},
  };
}

function hasReasoningContent(event) {
  return typeof event?.content === 'string' && event.content.trim().length > 0;
}

function shouldSuppressStreamEvent(event, existingEvents = []) {
  if (event?.eventType !== 'reasoning_summary') {
    return false;
  }

  if (!hasReasoningContent(event)) {
    return true;
  }

  return existingEvents.some(
    (existingEvent) =>
      existingEvent?.eventType === 'reasoning_delta' && hasReasoningContent(existingEvent),
  );
}

function extractReasoningTokenCount(event) {
  const tokenCount = event?.metadata?.reasoning_tokens;
  return Number.isFinite(tokenCount) && tokenCount > 0 ? tokenCount : null;
}

function reasoningGroupKey(event) {
  const streamMetadata = event?.metadata?.stream_metadata;
  if (!streamMetadata || typeof streamMetadata !== 'object') {
    return null;
  }

  const langgraphStep = streamMetadata.langgraph_step;
  const checkpointNs = typeof streamMetadata.checkpoint_ns === 'string'
    ? streamMetadata.checkpoint_ns
    : '';
  const nodeName = typeof streamMetadata.langgraph_node === 'string'
    ? streamMetadata.langgraph_node
    : '';

  if (Number.isFinite(langgraphStep)) {
    return `step:${langgraphStep}:${nodeName}:${checkpointNs}`;
  }
  if (checkpointNs || nodeName) {
    return `fallback:${nodeName}:${checkpointNs}`;
  }
  return null;
}

function normalizeStoredMessage(message, index) {
  if (!message || typeof message !== 'object') {
    return null;
  }

  const role = typeof message.role === 'string' ? message.role : 'assistant';
  const timestamp = typeof message.timestamp === 'number' ? message.timestamp : Date.now();
  let content = message.content;

  if (typeof content !== 'string') {
    if (content === null || content === undefined) {
      content = '';
    } else {
      try {
        content = JSON.stringify(content);
      } catch {
        content = String(content);
      }
    }
  }

  return {
    ...message,
    id: typeof message.id === 'string' ? message.id : `msg_restored_${index}_${timestamp}`,
    role,
    content,
    tokenCount: Number.isFinite(message.tokenCount) ? message.tokenCount : 0,
    reasoningTokenCount: Number.isFinite(message.reasoningTokenCount)
      ? message.reasoningTokenCount
      : 0,
    streamEvents: Array.isArray(message.streamEvents)
      ? message.streamEvents.reduce((events, event, eventIndex) => {
        const normalizedEvent = normalizeStreamEvent(event, eventIndex);
        if (!normalizedEvent || shouldSuppressStreamEvent(normalizedEvent, events)) {
          return events;
        }
        return events.concat(normalizedEvent);
      }, [])
      : [],
    timestamp,
  };
}

function createStreamEvent(streamEvent) {
  if (!streamEvent || typeof streamEvent !== 'object') {
    return null;
  }

  return {
    id: `evt_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    eventType: typeof streamEvent.event_type === 'string' ? streamEvent.event_type : 'stream_event',
    content: typeof streamEvent.content === 'string' ? streamEvent.content : '',
    metadata:
      streamEvent.metadata
      && typeof streamEvent.metadata === 'object'
      && !Array.isArray(streamEvent.metadata)
        ? streamEvent.metadata
        : {},
  };
}

function appendStreamEvent(events, nextEvent) {
  const currentEvents = Array.isArray(events) ? events : [];
  if (shouldSuppressStreamEvent(nextEvent, currentEvents)) {
    return currentEvents;
  }
  if (nextEvent.eventType === 'reasoning_delta') {
    const lastEvent = currentEvents[currentEvents.length - 1];
    if (
      lastEvent?.eventType === 'reasoning_delta'
      && reasoningGroupKey(lastEvent) !== null
      && reasoningGroupKey(lastEvent) === reasoningGroupKey(nextEvent)
    ) {
      const mergedContent = `${lastEvent.content || ''}${nextEvent.content || ''}`;
      const mergedMetadata = {
        ...lastEvent.metadata,
        ...nextEvent.metadata,
        text_length: mergedContent.length,
      };
      return currentEvents.slice(0, -1).concat({
        ...lastEvent,
        content: mergedContent,
        metadata: mergedMetadata,
      });
    }
  }
  const lastEvent = currentEvents[currentEvents.length - 1];
  if (
    lastEvent
    && lastEvent.eventType === nextEvent.eventType
    && lastEvent.content === nextEvent.content
    && JSON.stringify(lastEvent.metadata) === JSON.stringify(nextEvent.metadata)
  ) {
    return currentEvents;
  }
  const next = currentEvents.concat(nextEvent);
  // Token deltas have their own bounded budget so a long answer cannot
  // evict the tool/step events that motivated this feature.
  const tokenLimit = MAX_TOKEN_DELTAS_PER_MESSAGE;
  let tokenCount = 0;
  for (let i = next.length - 1; i >= 0; i -= 1) {
    if (next[i].eventType === 'message_delta') {
      tokenCount += 1;
      if (tokenCount > tokenLimit) {
        next.splice(i, 1);
      }
    }
  }
  // Cap non-token events independently of token volume.
  const nonTokenLimit = MAX_STREAM_EVENTS_PER_MESSAGE;
  let nonTokenCount = 0;
  for (let i = next.length - 1; i >= 0; i -= 1) {
    if (next[i].eventType !== 'message_delta') {
      nonTokenCount += 1;
      if (nonTokenCount > nonTokenLimit) {
        next.splice(i, 1);
      }
    }
  }
  return next;
}

function stripEphemeralStreamEvents(message) {
  const events = Array.isArray(message?.streamEvents) ? message.streamEvents : [];
  if (events.length === 0) return message;
  const persistable = events.filter((event) =>
    PERSISTED_CATEGORIES.has(categoryForEventType(event.eventType))
  );
  if (persistable.length === events.length) {
    return message;
  }
  return { ...message, streamEvents: persistable };
}

function finalizeSupersededAssistant(message) {
  if (!message?.isStreaming) {
    return message;
  }

  const content = typeof message.content === 'string' ? message.content : '';
  const streamEvents = Array.isArray(message.streamEvents) ? message.streamEvents : [];
  const hasVisibleOutput = content.trim().length > 0 || streamEvents.length > 0;

  if (!hasVisibleOutput) {
    return null;
  }

  return {
    ...message,
    isStreaming: false,
    content: content.endsWith(CANCELLED_SUFFIX) ? content : `${content}${CANCELLED_SUFFIX}`,
  };
}

function ChatPage() {
  const { config } = useConfig();
  const {
    connectionState,
    sendMessageStream,
    cancelStream,
    getSession,
    createSession,
  } = useApi();
  const { show } = useNotifications();

  const [messages, setMessages] = useState(() => {
    const saved = localStorage.getItem(config.storage.chatHistory);
    if (!saved) {
      return [];
    }

    try {
      const parsed = JSON.parse(saved);
      if (!Array.isArray(parsed)) {
        return [];
      }
      return parsed
        .map((message, index) => normalizeStoredMessage(message, index))
        .filter(Boolean);
    } catch (error) {
      console.warn('Failed to load chat history:', error);
      return [];
    }
  });
  const [isGenerating, setIsGenerating] = useState(false);
  const [isClearingSession, setIsClearingSession] = useState(false);
  const [typingState, setTypingState] = useState('thinking');
  const [tokenCount, setTokenCount] = useState(0);
  const [sessionId, setSessionId] = useState(null);
  const [streamFilters, setStreamFilters] = useState(() =>
    loadStreamFilters(config.storage.streamFilters),
  );
  const messagesEndRef = useRef(null);
  const activeAssistantIdRef = useRef(null);

  const streamEventFilter = useCallback(
    (event) => isEventVisible(event, streamFilters),
    [streamFilters],
  );

  useEffect(() => {
    let cancelled = false;

    const ensureSession = async () => {
      try {
        const session = await getSession();
        if (!cancelled) {
          setSessionId(session.session_id);
        }
      } catch {
        try {
          const session = await createSession();
          if (!cancelled) {
            setSessionId(session.session_id);
          }
        } catch (error) {
          console.warn('[ChatPage] Failed to initialize session:', error);
        }
      }
    };

    ensureSession();
    return () => {
      cancelled = true;
    };
  }, [createSession, getSession]);

  // Save chat history to localStorage (strip ephemeral stream events to
  // keep storage bounded; tokens/debug events are session-only).
  useEffect(() => {
    if (messages.length > 0) {
      const persistable = messages.slice(-100).map(stripEphemeralStreamEvents);
      localStorage.setItem(config.storage.chatHistory, JSON.stringify(persistable));
    }
  }, [messages, config.storage.chatHistory]);

  // Scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isGenerating]);

  // Track recently added scheduled messages to deduplicate
  const recentScheduledContent = useRef(new Set());

  // Handle chat events (from /chat SSE stream)
  const handleChatEvent = useCallback((event) => {
    if (!event?.data) return;

    // Handle scheduled/background messages from the agent.
    // Prefer agent_message/agent_question over scheduled_response as they have cleaner content.
    if (
      event.type === 'agent_message'
      || event.type === 'agent_question'
      || event.type === 'scheduled_response'
    ) {
      const content = event.data?.content || '';
      const metadata = event.data?.metadata || {};
      const isQuestion = event.type === 'agent_question';

      // Clean up the content (remove "SAY: " prefix if present)
      let cleanContent = content;
      if (cleanContent.startsWith('SAY: ')) {
        cleanContent = cleanContent.slice(5);
      }

      if (cleanContent) {
        // Deduplicate: check if we recently added a message with the same content
        const contentKey = cleanContent.slice(0, 100);
        if (recentScheduledContent.current.has(contentKey)) {
          // Already added this message, skip
          return;
        }

        // Mark as recently added (auto-expire after 5 seconds)
        recentScheduledContent.current.add(contentKey);
        setTimeout(() => {
          recentScheduledContent.current.delete(contentKey);
        }, 5000);

        const scheduledMessage = {
          id: `msg_${Date.now()}_scheduled`,
          role: 'assistant',
          content: cleanContent,
          timestamp: Date.now(),
          isScheduled: true,
          isQuestion,
          scheduleId: metadata?.schedule_id,
          originalRequest: metadata?.original_request,
        };

        setMessages(prev => [...prev, scheduledMessage]);

        // Show a notification
        show(
          isQuestion ? 'warning' : 'info',
          metadata?.title || (isQuestion ? 'Question from Progenitor' : 'Scheduled Message'),
          metadata?.schedule_id
            ? `Reminder: ${cleanContent.slice(0, 50)}...`
            : cleanContent.slice(0, 100)
        );
      }
    }

    // Handle alerts
    if (event.type === 'alert') {
      show(event.data?.type || 'info', event.data?.title || 'Alert', event.data?.message || '');
    }

    if (event.type === 'message_queued') {
      show(
        'info',
        'Message queued',
        event.data?.content || 'A previous message is still running; your message will be handled next.'
      );
    }
  }, [show]);

  // Connect to chat SSE stream
  useEventsSSE(handleChatEvent);

  const handleSend = useCallback(async (content) => {
    const userMessage = {
      id: `msg_${Date.now()}`,
      role: 'user',
      content,
      timestamp: Date.now()
    };

    setMessages(prev => [...prev, userMessage]);
    setIsGenerating(true);
    setTypingState('thinking');
    setTokenCount(0);

    let assistantContent = '';
    const assistantId = `msg_${Date.now()}_assistant`;
    const previousAssistantId = activeAssistantIdRef.current;
    activeAssistantIdRef.current = assistantId;

    // Add placeholder assistant message; clear isStreaming on any prior
    // placeholder that is being superseded so the spinner doesn't stick.
    setMessages(prev => {
      const next = previousAssistantId
        ? prev
          .map((m) => (
            m.id === previousAssistantId
              ? finalizeSupersededAssistant(m)
              : m
          ))
          .filter(Boolean)
        : prev;
      return [...next, {
        id: assistantId,
        role: 'assistant',
        content: '',
        timestamp: Date.now(),
        tokenCount: 0,
        reasoningTokenCount: 0,
        isStreaming: true,
        streamEvents: [],
      }];
    });

    const history = messages.slice(-10).map(m => ({
      role: m.role,
      content: m.content
    }));

    try {
      const isStale = () => activeAssistantIdRef.current !== assistantId;
      await sendMessageStream(
        content,
        history,
        // onChunk
        (chunk) => {
          if (isStale()) return;
          setTypingState('generating');
          assistantContent += chunk;
          setTokenCount(prev => prev + 1);
          setMessages(prev => prev.map(m =>
            m.id === assistantId
              ? { ...m, content: assistantContent, tokenCount: (m.tokenCount || 0) + 1 }
              : m
          ));
        },
        // onComplete
        (status) => {
          if (isStale()) return;
          setMessages(prev => prev.map(m =>
            m.id === assistantId
              ? { ...m, isStreaming: false, content: assistantContent + (status === 'cancelled' ? ' [Cancelled]' : '') }
              : m
          ));
          setIsGenerating(false);
        },
        // onError
        (error) => {
          if (isStale()) return;
          show('error', 'Chat Error', error.message);
          setMessages(prev => prev.map(m =>
            m.id === assistantId
              ? { ...m, isStreaming: false, error: true, content: `Error: ${error.message}` }
              : m
          ));
          setIsGenerating(false);
        },
        // onStreamEvent
        (streamEvent) => {
          // Discard frames that arrive after this request was superseded
          // (cancel/retry) so they cannot attach to a newer message.
          if (isStale()) {
            return;
          }
          const nextEvent = createStreamEvent(streamEvent);
          if (!nextEvent) {
            return;
          }

          if (nextEvent.eventType === 'reasoning_delta') {
            setTypingState('thinking');
          } else if (
            nextEvent.eventType !== 'message_delta'
            && nextEvent.eventType !== 'reasoning_summary'
          ) {
            setTypingState('tool-use');
          }
          const reasoningTokenCount = extractReasoningTokenCount(nextEvent);
          setMessages(prev => prev.map(m =>
            m.id === assistantId
              ? {
                ...m,
                streamEvents: appendStreamEvent(m.streamEvents, nextEvent),
                reasoningTokenCount: reasoningTokenCount ?? m.reasoningTokenCount ?? 0,
              }
              : m
          ));
        },
      );
    } catch (error) {
      show('error', 'Chat Error', error.message);
      setIsGenerating(false);
    }
  }, [messages, sendMessageStream, show]);

  const handleStop = useCallback(() => {
    cancelStream();
  }, [cancelStream]);

  const handleQuickAction = useCallback((message) => {
    handleSend(message);
  }, [handleSend]);

  const handleCommand = useCallback(async (commandName) => {
    if (commandName !== 'clear') {
      return;
    }

    const previousAssistantId = activeAssistantIdRef.current;
    setIsClearingSession(true);
    activeAssistantIdRef.current = null;
    recentScheduledContent.current.clear();
    cancelStream();

    try {
      const session = await createSession();
      setMessages([]);
      setIsGenerating(false);
      setTypingState('thinking');
      setTokenCount(0);
      setSessionId(session.session_id);
      localStorage.removeItem(config.storage.chatHistory);
      show('success', 'Conversation cleared', 'Started a fresh chat web session.');
    } catch (error) {
      if (previousAssistantId) {
        setMessages((prev) => prev
          .map((message) => (
            message.id === previousAssistantId
              ? finalizeSupersededAssistant(message)
              : message
          ))
          .filter(Boolean));
      }
      setIsGenerating(false);
      setTypingState('thinking');
      setTokenCount(0);
      show('error', 'Clear failed', error.message);
    } finally {
      setIsClearingSession(false);
    }
  }, [cancelStream, config.storage.chatHistory, createSession, show]);

  return (
    <div className="app-container">
      <Header connectionState={connectionState} />
      <main className="main-content">
        <div className="chat-container">
          <div className="chat-toolbar">
            <StreamFilterMenu
              storageKey={config.storage.streamFilters}
              filters={streamFilters}
              onChange={setStreamFilters}
            />
          </div>
          <div className="chat-messages">
            {messages.length === 0 ? (
              <div className="welcome-message">
                <h2>Welcome to Progenitor Chat</h2>
                <p>Start a conversation with the Progenitor AI agent.</p>
                {sessionId && <p>Session: {sessionId}</p>}
                <QuickActions onAction={handleQuickAction} />
              </div>
            ) : (
              <>
                {messages.map((msg) => (
                  <Message
                    key={msg.id}
                    message={msg}
                    streamEventFilter={streamEventFilter}
                  />
                ))}
                {isGenerating && (
                  <TypingIndicator state={typingState} tokenCount={tokenCount} />
                )}
              </>
            )}
            <div ref={messagesEndRef} />
          </div>
          <ChatInput
            onSend={handleSend}
            onStop={handleStop}
            onCommand={handleCommand}
            isGenerating={isGenerating}
            disabled={connectionState === 'error' || isClearingSession}
          />
        </div>
      </main>
    </div>
  );
}

export default ChatPage;
