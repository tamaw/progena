import { BROWSER_STORAGE_KEY } from '../../lib/browserContracts';

export const SOLUTIONS_STORAGE_KEY = BROWSER_STORAGE_KEY.SOLUTIONS;

export const DEFAULT_SOLUTIONS = [
  {
    id: 'main',
    name: 'main',
    summary: 'Primary Progena AI solution for the local app workspace.',
    projects: [
      {
        id: 'backend-runtime',
        name: 'Backend runtime',
        summary: 'FastAPI, SSE, scheduling, state persistence, and agent runtime work.',
        status: 'active',
        accent: '#58a6ff',
      },
      {
        id: 'frontend-chrome',
        name: 'Frontend chrome',
        summary: 'React shell, navigation, panels, and mock app surfaces.',
        status: 'designing',
        accent: '#a78bfa',
      },
      {
        id: 'agent-workspace',
        name: 'Agent workspace',
        summary: 'Canvas flows, prompts, tools, and reusable skill authoring.',
        status: 'planning',
        accent: '#22a06b',
      },
    ],
  },
];

export function slugifyProjectName(name) {
  return String(name || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '') || 'new-project';
}

function withUniqueProjectId(baseId, projects) {
  const existing = new Set(projects.map((project) => project.id));
  if (!existing.has(baseId)) {
    return baseId;
  }

  let index = 2;
  while (existing.has(`${baseId}-${index}`)) {
    index += 1;
  }
  return `${baseId}-${index}`;
}

function withUniqueSolutionId(baseId, solutions) {
  const existing = new Set(solutions.map((solution) => solution.id));
  if (!existing.has(baseId)) {
    return baseId;
  }

  let index = 2;
  while (existing.has(`${baseId}-${index}`)) {
    index += 1;
  }
  return `${baseId}-${index}`;
}

export function createProjectFromName(name, existingProjects = []) {
  const projectName = String(name || '').trim() || 'New project';
  const id = withUniqueProjectId(slugifyProjectName(projectName), existingProjects);
  return {
    id,
    name: projectName,
    summary: 'New project workspace ready for planning and kanban tracking.',
    status: 'new',
    accent: '#ef6c2a',
  };
}

export function createSolutionFromName(name, summary, existingSolutions = []) {
  const solutionName = String(name || '').trim() || 'New solution';
  return {
    id: withUniqueSolutionId(slugifyProjectName(solutionName), existingSolutions),
    name: solutionName,
    summary: String(summary || '').trim() || 'Solution workspace ready for projects and kanban boards.',
    projects: [],
  };
}

export function normalizeSolutions(solutions) {
  if (!Array.isArray(solutions) || solutions.length === 0) {
    return DEFAULT_SOLUTIONS;
  }

  return solutions.map((solution) => ({
    ...solution,
    projects: Array.isArray(solution.projects) ? solution.projects : [],
  }));
}

export function loadStoredSolutions() {
  if (typeof window === 'undefined') {
    return DEFAULT_SOLUTIONS;
  }

  try {
    const rawValue = window.localStorage.getItem(SOLUTIONS_STORAGE_KEY);
    if (!rawValue) {
      return DEFAULT_SOLUTIONS;
    }
    return normalizeSolutions(JSON.parse(rawValue));
  } catch (error) {
    console.warn('Unable to read stored solutions. Using default project tree.', error);
    return DEFAULT_SOLUTIONS;
  }
}

export function saveStoredSolutions(solutions) {
  if (typeof window === 'undefined') {
    return;
  }

  window.localStorage.setItem(SOLUTIONS_STORAGE_KEY, JSON.stringify(normalizeSolutions(solutions)));
}

export function findSolutionProject(solutions, solutionId, projectId) {
  const solution = normalizeSolutions(solutions).find((item) => item.id === solutionId);
  const project = solution?.projects.find((item) => item.id === projectId);
  return { solution, project };
}

export function titleFromProjectId(projectId) {
  return String(projectId || 'Project')
    .split('-')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}
