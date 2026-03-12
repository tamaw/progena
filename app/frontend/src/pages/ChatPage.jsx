import { useState, useEffect, useRef, useCallback } from 'react';
import { useApi } from '../hooks/useApi';
import { useEventsSSE, useStatusSSE } from '../hooks/useSSE';
import { useConfig } from '../hooks/useConfig';
import { useHealth } from '../hooks/useHealth';
import { useNotifications } from '../hooks/useNotifications';
import Header from '../components/layout/Header';
import Sidebar from '../components/layout/Sidebar';
import ChatInput from '../components/chat/ChatInput';
import Message from '../components/chat/Message';
import TypingIndicator from '../components/chat/TypingIndicator';
import QuickActions from '../components/chat/QuickActions';

function ChatPage() {
  const { config } = useConfig();
  useHealth();
  const { connectionState, sendMessageStream, cancelStream } = useApi();
  const { show } = useNotifications();

  const [messages, setMessages] = useState(() => {
    const saved = localStorage.getItem(config.storage.chatHistory);
    if (!saved) {
      return [];
    }

    try {
      return JSON.parse(saved);
    } catch (error) {
      console.warn('Failed to load chat history:', error);
      return [];
    }
  });
  const [isGenerating, setIsGenerating] = useState(false);
  const [typingState, setTypingState] = useState('thinking');
  const [tokenCount, setTokenCount] = useState(0);
  const [status, setStatus] = useState(null);
  const messagesEndRef = useRef(null);

  // Save chat history to localStorage
  useEffect(() => {
    if (messages.length > 0) {
      localStorage.setItem(config.storage.chatHistory, JSON.stringify(messages.slice(-100)));
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

    // Handle scheduled/background messages from the agent
    // Prefer agent_message over scheduled_response as it has cleaner content
    if (event.type === 'agent_message' || event.type === 'scheduled_response') {
      const content = event.data?.content || '';
      const metadata = event.data?.metadata || {};

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
          scheduleId: metadata?.schedule_id,
          originalRequest: metadata?.original_request,
        };

        setMessages(prev => [...prev, scheduledMessage]);

        // Show a notification
        show('info', 'Scheduled Message',
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
  }, [show]);

  // Handle status events (from /status/stream SSE stream)
  const handleStatusEvent = useCallback((event) => {
    if (!event?.data) return;

    if (event.type === 'connected' || event.type === 'status' || event.type === 'heartbeat') {
      setStatus(event.data);
    }
  }, []);

  // Connect to both SSE streams
  useEventsSSE(handleChatEvent);
  useStatusSSE(handleStatusEvent);

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

    // Add placeholder assistant message
    setMessages(prev => [...prev, {
      id: assistantId,
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
      isStreaming: true
    }]);

    const history = messages.slice(-10).map(m => ({
      role: m.role,
      content: m.content
    }));

    try {
      await sendMessageStream(
        content,
        history,
        // onChunk
        (chunk) => {
          setTypingState('generating');
          assistantContent += chunk;
          setTokenCount(prev => prev + 1);
          setMessages(prev => prev.map(m =>
            m.id === assistantId
              ? { ...m, content: assistantContent }
              : m
          ));
        },
        // onComplete
        (status) => {
          setMessages(prev => prev.map(m =>
            m.id === assistantId
              ? { ...m, isStreaming: false, content: assistantContent + (status === 'cancelled' ? ' [Cancelled]' : '') }
              : m
          ));
          setIsGenerating(false);
        },
        // onError
        (error) => {
          show('error', 'Chat Error', error.message);
          setMessages(prev => prev.map(m =>
            m.id === assistantId
              ? { ...m, isStreaming: false, error: true, content: `Error: ${error.message}` }
              : m
          ));
          setIsGenerating(false);
        }
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

  return (
    <div className="app-container">
      <Header connectionState={connectionState} />
      <main className="main-content">
        <Sidebar status={status} />
        <div className="chat-container">
          <div className="chat-messages">
            {messages.length === 0 ? (
              <div className="welcome-message">
                <h2>Welcome to Progenitor Chat</h2>
                <p>Start a conversation with the Progenitor AI agent.</p>
                <QuickActions onAction={handleQuickAction} />
              </div>
            ) : (
              <>
                {messages.map((msg) => (
                  <Message key={msg.id} message={msg} />
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
            isGenerating={isGenerating}
            disabled={connectionState === 'error'}
          />
        </div>
      </main>
    </div>
  );
}

export default ChatPage;
