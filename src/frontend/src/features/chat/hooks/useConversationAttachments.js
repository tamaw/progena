import { useState, useRef, useCallback } from 'react';

export function useConversationAttachments({ connectionState, isClearingSession, chatInputRef }) {
  const [isDraggingOverChat, setIsDraggingOverChat] = useState(false);
  const chatDragDepthRef = useRef(0);
  const handleChatDragEnter = useCallback((e) => {
    if (connectionState === 'error' || isClearingSession) return;
    if (!Array.from(e.dataTransfer?.types || []).includes('Files')) return;
    e.preventDefault();
    chatDragDepthRef.current += 1;
    setIsDraggingOverChat(true);
  }, [connectionState, isClearingSession]);
  const handleChatDragOver = useCallback((e) => {
    if (connectionState === 'error' || isClearingSession) return;
    if (!Array.from(e.dataTransfer?.types || []).includes('Files')) return;
    e.preventDefault();
  }, [connectionState, isClearingSession]);
  const handleChatDragLeave = useCallback((e) => {
    e.preventDefault();
    chatDragDepthRef.current = Math.max(0, chatDragDepthRef.current - 1);
    if (chatDragDepthRef.current === 0) {
      setIsDraggingOverChat(false);
    }
  }, []);
  const handleChatDrop = useCallback((e) => {
    if (connectionState === 'error' || isClearingSession) return;
    e.preventDefault();
    chatDragDepthRef.current = 0;
    setIsDraggingOverChat(false);
    chatInputRef.current?.addFiles(e.dataTransfer?.files);
  }, [chatInputRef, connectionState, isClearingSession]);

  return { isDraggingOverChat, handleChatDragEnter, handleChatDragOver, handleChatDragLeave, handleChatDrop };
}
