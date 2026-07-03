import { useCallback } from 'react';
import { useApiClient } from './useApiClient';

/** Prompt library read/update (Prompts page, prompt library panel). */
export function usePromptsApi() {
  const { request } = useApiClient();

  const getPrompts = useCallback((agent = null) => {
    const search = agent ? `?${new URLSearchParams({ agent }).toString()}` : '';
    return request(search ? `/api/ui/prompts${search}` : 'prompts');
  }, [request]);

  const updatePromptContent = useCallback(
    (path, content) => request('/api/ui/prompts/content', {
      method: 'PUT',
      body: JSON.stringify({ path, content }),
    }),
    [request],
  );

  return { getPrompts, updatePromptContent };
}
