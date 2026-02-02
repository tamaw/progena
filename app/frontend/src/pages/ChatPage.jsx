import { useState, useEffect, useRef, useCallback } from 'react';
import { useApi } from '../hooks/useApi';
import { useSSE } from '../hooks/useSSE';
import { useConfig } from '../context/ConfigContext';
import { useNotifications } from '../context/NotificationContext';
import Header from '../components/layout/Header';
import Sidebar from '../components/layout/Sidebar';
import ChatInput from '../components/chat/ChatInput';
import Message from '../components/chat/Message';
import TypingIndicator from '../components/chat/TypingIndicator';
import QuickActions from '../components/chat/QuickActions';

function ChatPage() {
  const { config } = useConfig();
  const { connectionState, sendMessageStream, cancelStream, getHealth } = useApi();
  const { show } = useNotifications();

  const [messages, setMessages] = useState([]);
  const [isGenerating, setIsGenerating] = useState(false);
  const [typingState, setTypingState] = useState('thinking');
  const [tokenCount, setTokenCount] = useState(0);
  const [status, setStatus] = useState(null);
  const messagesEndRef = useRef(null);

  // Load chat history from localStorage
  useEffect(() => {
    const saved = localStorage.getItem(config.storage.chatHistory);
    if (saved) {
      try {
        setMessages(JSON.parse(saved));
      } catch (e) {
        console.warn('Failed to load chat history:', e);
      }
    }
  }, [config.storage.chatHistory]);

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

  // Keep connection state updated via lightweight health checks
  useEffect(() => {
    const fetchHealth = async () => {
      try {
        await getHealth();
      } catch (e) {
        // Silently fail
      }
    };

    fetchHealth();
    const interval = setInterval(fetchHealth, 5000);
    return () => clearInterval(interval);
  }, [getHealth]);

  const handleSSEEvent = useCallback((event) => {
    if (!event?.data) return;

    if (event.type === 'connected' || event.type === 'status' || event.type === 'heartbeat') {
      setStatus(event.data);
    }
  }, []);

  useSSE(handleSSEEvent);

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
