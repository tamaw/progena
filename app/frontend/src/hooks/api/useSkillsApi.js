import { useCallback } from 'react';
import { useApiClient } from './useApiClient';

/** Skill library read/update/create (Skills page). */
export function useSkillsApi() {
  const { request } = useApiClient();

  const getSkills = useCallback((agent = null) => {
    const search = agent ? `?${new URLSearchParams({ agent }).toString()}` : '';
    return request(search ? `/api/ui/skills${search}` : 'skills');
  }, [request]);

  const updateSkillContent = useCallback(
    (path, content) => request('/api/ui/skills/content', {
      method: 'PUT',
      body: JSON.stringify({ path, content }),
    }),
    [request],
  );

  const createSkill = useCallback(
    ({ agent, name, content }) => request('/api/ui/skills', {
      method: 'POST',
      body: JSON.stringify({ agent, name, content }),
    }),
    [request],
  );

  return { getSkills, updateSkillContent, createSkill };
}
