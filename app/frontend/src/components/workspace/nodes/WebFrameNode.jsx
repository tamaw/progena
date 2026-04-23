import { memo, useCallback, useMemo, useRef, useState } from 'react';
import { NodeResizer } from '@xyflow/react';
import { useWorkspaceActions, useWorkspaceMeta } from '../../../context/WorkspaceContext';
import WidgetTabs from './WidgetTabs';
import NodeHelpButton from './NodeHelpButton';
import { resolveWidgetDefaultTab } from './widgetTabUtils';

function WebFrameNode({ id, data, selected }) {
  const { updateNodeData, toggleExpanded } = useWorkspaceActions();
  const { getWidgetTabs, getWidgetDefaultTab, getWidgetHelpText } = useWorkspaceMeta();
  const { expanded } = data;
  const iframeRef = useRef(null);
  const tabs = getWidgetTabs('webframe');
  const helpText = getWidgetHelpText('webframe');
  const defaultTab = useMemo(
    () => resolveWidgetDefaultTab(tabs, getWidgetDefaultTab('webframe'), 'preview'),
    [getWidgetDefaultTab, tabs]
  );
  const [activeTab, setActiveTab] = useState(defaultTab);
  const resolvedActiveTab = useMemo(
    () => resolveWidgetDefaultTab(tabs, activeTab, defaultTab),
    [activeTab, defaultTab, tabs]
  );

  const onToggle = useCallback(() => {
    toggleExpanded(id);
  }, [id, toggleExpanded]);

  const onHtmlChange = useCallback((e) => {
    updateNodeData(id, { html: e.target.value });
  }, [id, updateNodeData]);

  const onUrlChange = useCallback((e) => {
    updateNodeData(id, { url: e.target.value });
  }, [id, updateNodeData]);

  const onSourceTypeChange = useCallback((e) => {
    updateNodeData(id, { sourceType: e.target.value });
  }, [id, updateNodeData]);

  const reload = useCallback(() => {
    if (iframeRef.current) {
      const currentSrc = iframeRef.current.src;
      iframeRef.current.src = '';
      iframeRef.current.src = currentSrc;
    }
  }, []);

  const sandboxAttrs = useMemo(() => {
    const attrs = [];
    const allowScripts = Boolean(data.sandbox?.allowScripts);
    const allowSameOrigin = Boolean(data.sandbox?.allowSameOrigin) && !allowScripts;
    if (allowScripts) attrs.push('allow-scripts');
    if (allowSameOrigin) attrs.push('allow-same-origin');
    if (data.sandbox?.allowForms) attrs.push('allow-forms');
    if (data.sandbox?.allowPopups) attrs.push('allow-popups');
    return attrs.join(' ');
  }, [data.sandbox]);

  const iframeSrc = useMemo(() => {
    if (data.sourceType === 'remote_url' || data.sourceType === 'internal_url') {
      return data.url || 'about:blank';
    }
    // Build HTML document from parts
    const css = data.css ? `<style>${data.css}</style>` : '';
    const js = data.js ? `<script>${data.js}</` + `script>` : '';
    const html = data.html || '';
    // If the HTML already has <html> tags, inject css/js into it
    if (html.includes('<html') || html.includes('<head') || html.includes('<body')) {
      const doc = html.replace('</head>', `${css}</head>`).replace('</body>', `${js}</body>`);
      return `data:text/html;charset=utf-8,${encodeURIComponent(doc)}`;
    }
    const fullDoc = `<!DOCTYPE html><html><head><meta charset="utf-8">${css}</head><body>${html}${js}</body></html>`;
    return `data:text/html;charset=utf-8,${encodeURIComponent(fullDoc)}`;
  }, [data.html, data.css, data.js, data.url, data.sourceType]);

  const sourceTypeLabel = data.sourceType === 'html' ? 'HTML'
    : data.sourceType === 'bundle' ? 'Bundle'
    : data.sourceType === 'internal_url' ? 'Internal'
    : data.sourceType === 'remote_url' ? 'Remote'
    : 'HTML';

  return (
    <>
      <NodeResizer
        minWidth={320}
        minHeight={200}
        isVisible={selected}
        lineClassName="react-flow__resize-control line"
        handleClassName="react-flow__resize-control handle"
      />
      <div className={`workspace-node ${selected ? 'selected' : ''}`}>
        <div className="node-title-bar drag-handle">
          <div className="node-type-icon webframe">W</div>
          <div className="node-title">{data.title}</div>
          <span className="webframe-source-badge">{sourceTypeLabel}</span>
          <button className="node-expand-btn" onClick={onToggle}>
            {expanded ? '\u25B2' : '\u25BC'}
          </button>
        </div>

        <div className="node-body">
          {expanded ? (
            <div className="node-body-expanded">
              <div className="webframe-container">
                <WidgetTabs tabs={tabs} activeTab={resolvedActiveTab} onChange={setActiveTab} />
                {resolvedActiveTab === 'preview' ? (
                  <iframe
                    ref={iframeRef}
                    className="webframe-iframe nodrag nowheel"
                    src={iframeSrc}
                    sandbox={sandboxAttrs}
                    title={data.title}
                  />
                ) : (
                  <>
                    {(data.sourceType === 'remote_url' || data.sourceType === 'internal_url') ? (
                      <input
                        className="nodrag"
                        style={{
                          background: 'var(--color-bg-primary)',
                          color: 'var(--color-text-primary)',
                          border: '1px solid var(--color-border)',
                          borderRadius: 'var(--border-radius-sm)',
                          padding: '4px 8px',
                          fontSize: 'var(--font-size-sm)',
                          fontFamily: 'var(--font-family-mono)',
                        }}
                        value={data.url || ''}
                        onChange={onUrlChange}
                        placeholder="Enter URL..."
                      />
                    ) : (
                      <textarea
                        className="webframe-source-editor nodrag nowheel"
                        value={data.html || ''}
                        onChange={onHtmlChange}
                        placeholder="Enter HTML..."
                        spellCheck={false}
                      />
                    )}
                  </>
                )}
              </div>
            </div>
          ) : (
            <div className="node-body-compact">
              <iframe
                className="webframe-preview nodrag"
                src={iframeSrc}
                sandbox={sandboxAttrs}
                title={data.title}
                style={{ pointerEvents: 'none', height: '100%' }}
              />
            </div>
          )}
        </div>

        {expanded && (
          <div className="node-actions">
            <button className="node-action-btn" onClick={reload}>Reload</button>
            <select
              className="code-language-select nodrag"
              value={data.sourceType || 'html'}
              onChange={onSourceTypeChange}
              style={{ marginLeft: 'auto' }}
            >
              <option value="html">HTML</option>
              <option value="bundle">Bundle</option>
              <option value="internal_url">Internal URL</option>
              <option value="remote_url">Remote URL</option>
            </select>
            <NodeHelpButton description={helpText} title={data.title} />
          </div>
        )}
      </div>
    </>
  );
}

export default memo(WebFrameNode);
