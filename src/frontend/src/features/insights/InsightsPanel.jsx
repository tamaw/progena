import CompactInsights from './CompactInsights';
import { TopNavPane } from '../../components/panels/PanelFrame';

export function InsightsPanel({ onClose, isPinned, onTogglePin, isOpen }) {
  return (
    <TopNavPane
      title="Token consumption"
      onClose={onClose}
      isPinned={isPinned}
      onTogglePin={onTogglePin}
      isOpen={isOpen}
      className="topnav-pane--insights"
      bodyClassName="ins-pane__body"
    >
      <CompactInsights onNavigate={onClose} />
    </TopNavPane>
  );
}
