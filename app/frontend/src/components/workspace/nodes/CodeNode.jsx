import { memo, useCallback } from 'react';
import { NodeResizer } from '@xyflow/react';
import { useWorkspaceContext } from '../../../hooks/useWorkspaceContext';

const LANGUAGES = [
  'javascript', 'python', 'html', 'css', 'bash',
  'json', 'yaml', 'markdown', 'typescript', 'sql',
];

function CodeNode({ id, data, selected }) {
  const { updateNodeData, toggleExpanded } = useWorkspaceContext();
  const { expanded } = data;

  const onTitleChange = useCallback((e) => {
    updateNodeData(id, { title: e.target.value });
  }, [id, updateNodeData]);

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
      <div className={`workspace-node ${selected ? 'selected' : ''}`}>
        <div className="node-title-bar">
          <div className="node-type-icon code">&lt;&gt;</div>
          <input
            className="node-title nodrag"
            value={data.title}
            onChange={onTitleChange}
          />
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
              <div className="code-editor-container">
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
                {data.outputRefs?.length > 0 && (
                  <div className="code-output">
                    {data.outputRefs.join('\n')}
                  </div>
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
            <button className="node-action-btn primary">Run</button>
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
