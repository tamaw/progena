import { memo, useCallback, useMemo, useState } from 'react';
import { Handle, NodeResizer, Position } from '@xyflow/react';
import { useWorkspaceActions, useWorkspaceMeta } from '../../../context/WorkspaceContext';
import WidgetTabs from './WidgetTabs';
import { resolveWidgetDefaultTab } from './widgetTabUtils';

const LANGUAGES = [
  'javascript', 'python', 'html', 'css', 'bash',
  'json', 'yaml', 'markdown', 'typescript', 'sql',
];

function CodeNode({ id, data, selected }) {
  const { updateNodeData, toggleExpanded, runCodeNode } = useWorkspaceActions();
  const { getWidgetTabs, getWidgetDefaultTab } = useWorkspaceMeta();
  const { expanded } = data;
  const tabs = getWidgetTabs('code');
  const defaultTab = useMemo(
    () => resolveWidgetDefaultTab(tabs, getWidgetDefaultTab('code'), 'editor'),
    [getWidgetDefaultTab, tabs]
  );
  const [activeTab, setActiveTab] = useState(defaultTab);
  const resolvedActiveTab = useMemo(
    () => resolveWidgetDefaultTab(tabs, activeTab, defaultTab),
    [activeTab, defaultTab, tabs]
  );

  const onContentChange = useCallback((e) => {
    updateNodeData(id, { content: e.target.value });
  }, [id, updateNodeData]);

  const onLanguageChange = useCallback((e) => {
    updateNodeData(id, { language: e.target.value });
  }, [id, updateNodeData]);

  const onToggle = useCallback(() => {
    toggleExpanded(id);
  }, [id, toggleExpanded]);

  const onKeyDown = useCallback((e) => {
    if (e.key === 'Tab') {
      e.preventDefault();
      const textarea = e.target;
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      const value = textarea.value;
      const newValue = value.substring(0, start) + '  ' + value.substring(end);
      updateNodeData(id, { content: newValue });
      // Restore cursor position after React re-render
      requestAnimationFrame(() => {
        textarea.selectionStart = textarea.selectionEnd = start + 2;
      });
    }
  }, [id, updateNodeData]);

  const codePreview = data.content
    ? data.content.split('\n').slice(0, 4).join('\n')
    : '// empty';

  return (
    <>
      <NodeResizer
        minWidth={280}
        minHeight={160}
        isVisible={selected}
        lineClassName="react-flow__resize-control line"
        handleClassName="react-flow__resize-control handle"
      />
      <Handle type="target" position={Position.Left} className="workspace-handle" />
      <div className={`workspace-node ${selected ? 'selected' : ''}`}>
        <div className="node-title-bar drag-handle">
          <div className="node-type-icon code">&lt;&gt;</div>
          <div className="node-title">{data.title}</div>
          <span className="code-language-badge">{data.language || 'js'}</span>
          {data.status !== 'idle' && (
            <span className={`node-status-badge ${data.status}`}>{data.status}</span>
          )}
          <button className="node-expand-btn" onClick={onToggle}>
            {expanded ? '\u25B2' : '\u25BC'}
          </button>
        </div>

        <div className="node-body">
          {expanded ? (
            <div className="node-body-expanded">
              <WidgetTabs tabs={tabs} activeTab={resolvedActiveTab} onChange={setActiveTab} />
              <div className="code-editor-container">
                {resolvedActiveTab === 'output' ? (
                  <div className="code-output">
                    {data.outputRefs?.length > 0 ? data.outputRefs.join('\n') : 'No output yet.'}
                  </div>
                ) : (
                  <>
                    <div className="code-editor-toolbar">
                      <select
                        className="code-language-select nodrag"
                        value={data.language || 'javascript'}
                        onChange={onLanguageChange}
                      >
                        {LANGUAGES.map((lang) => (
                          <option key={lang} value={lang}>{lang}</option>
                        ))}
                      </select>
                    </div>
                    <textarea
                      className="code-editor nodrag nowheel"
                      value={data.content || ''}
                      onChange={onContentChange}
                      onKeyDown={onKeyDown}
                      placeholder="// Write your code here..."
                      spellCheck={false}
                    />
                  </>
                )}
              </div>
            </div>
          ) : (
            <div className="node-body-compact">
              <pre className="code-preview">{codePreview}</pre>
            </div>
          )}
        </div>

        {expanded && (
          <div className="node-actions">
            <button className="node-action-btn primary" onClick={() => runCodeNode(id)}>Run</button>
            {data.status === 'running' && (
              <button className="node-action-btn danger">Stop</button>
            )}
          </div>
        )}
      </div>
    </>
  );
}

export default memo(CodeNode);
