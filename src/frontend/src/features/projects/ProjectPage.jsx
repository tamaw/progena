import { useMemo } from 'react';
import { useParams } from 'react-router-dom';
import KanbanPage from '../tasks/KanbanPage';
import { findSolutionProject, loadStoredSolutions, titleFromProjectId } from './solutions';

export default function ProjectPage() {
  const { solutionId = 'main', projectId = 'backend-runtime' } = useParams();
  const { solution, project } = useMemo(
    () => findSolutionProject(loadStoredSolutions(), solutionId, projectId),
    [projectId, solutionId],
  );

  const solutionName = solution?.name || solutionId;
  const projectName = project?.name || titleFromProjectId(projectId);

  return (
    <div className="project-page">
      <KanbanPage
        context={{
          listTitle: 'Kanban',
          newCardLabel: '+ New card',
          projectId,
          projectName,
          solutionName,
        }}
      />
    </div>
  );
}
