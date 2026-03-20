import { memo } from 'react';

function WidgetTabs({ tabs, activeTab, onChange }) {
  if (!tabs || tabs.length <= 1) {
    return null;
  }

  return (
    <div className="node-tabs nodrag nowheel">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          className={`node-tab ${activeTab === tab.id ? 'active' : ''}`}
          onClick={() => onChange(tab.id)}
          type="button"
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

export default memo(WidgetTabs);
