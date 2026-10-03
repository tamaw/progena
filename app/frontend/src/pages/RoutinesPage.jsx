import { AgentWorkspace } from './AgentsPage';

const ROUTINES_PAGE = {
  kind: 'routines',
  basePath: '/routines',
  listLabel: 'Routines',
  createLabel: '+ New',
  searchPlaceholder: 'Search routines...',
  sortTitle: 'Sort routines',
};

export default function RoutinesPage() {
  return <AgentWorkspace page={ROUTINES_PAGE} />;
}
