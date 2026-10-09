import React, { useContext, useRef, useState, useMemo, useLayoutEffect, useEffect } from 'react';
import { MissingFieldsContext, GraphEditContext } from './graphEditorContext';
import hljs from 'highlight.js/lib/common';
import { checkExpressionSyntax } from '../../lib/restrictedExpression';
import {
  buildStatePathSuggestions,
  getExpressionAutocompleteContext,
  filterStatePathSuggestions,
  inputsToText,
  textToInputs,
  schemaFieldsToText,
  textToSchemaFields,
} from './graphFieldHelpers';
import { useSelector } from 'react-redux';
import { selectAgentProfiles } from '../../store/uiDataSlice';
import Select from '../common/Select';
import { createPortal } from 'react-dom';
import { useBufferedFieldText } from './useBufferedFieldText';


function ModelProfileField({ id, config, updateNodeConfig }) {
  const [profiles, setProfiles] = useState([]);
  const [error, setError] = useState('');
  const refresh = async () => {
    try {
      const response = await fetch('/api/ui/model-profiles');
      if (!response.ok) throw new Error('Unable to load model profiles');
      const data = await response.json();
      setProfiles(data.profiles || []);
      setError('');
    } catch (cause) { setError(cause.message); }
  };
  useEffect(() => {
    let active = true;
    fetch('/api/ui/model-profiles').then(async (response) => {
      if (!response.ok) throw new Error('Unable to load model profiles');
      const data = await response.json();
      if (active) setProfiles(data.profiles || []);
    }).catch((cause) => { if (active) setError(cause.message); });
    return () => { active = false; };
  }, []);
  const value = config?.model_profile || '';
  return (
    <label className="agents-graph-node__field">
      <span className="agents-graph-node__field-label">Model profile</span>
      <select className="agents-graph-node__toolinput" aria-label="Model profile" value={value}
        onFocus={refresh} onChange={(event) => updateNodeConfig(id, { model_profile: event.target.value || null })}>
        <option value="">Use default</option>
        {value && !profiles.some((profile) => profile.name === value) ? <option value={value}>{value} (unavailable)</option> : null}
        {profiles.map((profile) => <option key={profile.name} value={profile.name}>{profile.name} — {profile.deployment}</option>)}
      </select>
      {error ? <span role="alert">{error}</span> : null}
    </label>
  );
}

// Escapes `text` for use inside a `dangerouslySetInnerHTML` layer -- only the
// three characters that change HTML parsing matter here (this never lands in
// an attribute), unlike markdown.js's escapeHtml which also escapes quotes.
function escapeHtmlText(text) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// One config field bound to `data.config[field]` (a plain string value).
function ConfigField({ id, config, field, label, placeholder, multiline, updateNodeConfig }) {
  const missing = useContext(MissingFieldsContext);
  const value = config?.[field] || '';
  const required = missing.has(field);
  const onChange = (event) => updateNodeConfig(id, { [field]: event.target.value });
  const inputClass = (base) => `${base}${required ? ' agents-graph-node__input--required' : ''}`;
  return (
    <label className={`agents-graph-node__field${required ? ' agents-graph-node__field--required' : ''}`}>
      <span className="agents-graph-node__field-label">
        {label}
        {required ? <span className="agents-graph-node__required" title="Required before this routine can be saved">Required</span> : null}
      </span>
      {multiline ? (
        <textarea className={inputClass('agents-graph-node__prompt')} value={value} placeholder={placeholder} onChange={onChange} aria-required={required || undefined} />
      ) : (
        <input className={inputClass('agents-graph-node__toolinput')} value={value} placeholder={placeholder} onChange={onChange} aria-required={required || undefined} />
      )}
    </label>
  );
}

// `Expression` fields (eval/condition/switch config, Loop's loop_guard) hold
// a restricted Python expression -- worth highlighting the same way the
// "Edit as JSON" panel does: a transparent <textarea> stacked over a
// highlight.js `<pre>` so syntax colors show through while the caret still
// lands in a real, editable, screen-reader-visible textarea underneath. A
// second, equally transparent `<pre>` sits between the two: same text, but
// every character is `color: transparent` except the span
// `checkExpressionSyntax` flags as the first construct outside
// `expression.py`'s whitelist, which gets a red wavy underline via
// `text-decoration-color` (independent of the (transparent) text color, so
// the squiggly shows without a second copy of the glyphs).
//
// `autocomplete` layers a caret-anchored state-path menu on top -- open the
// moment the field is focused (showing every candidate), narrowing as the
// author types. Expressions resolve names directly against full routine
// state now (see expression.py's `ast.Attribute` handling), so there's no
// separate `inputs` binding step left to author; the same predecessor/
// response_schema suggestions just get typed straight into the expression.
export function ExpressionField({ id, config, field, label, placeholder, updateNodeConfig, autocomplete = false }) {
  const missing = useContext(MissingFieldsContext);
  const graph = useContext(GraphEditContext);
  const value = config?.[field] || '';
  const required = missing.has(field);
  const highlightRef = useRef(null);
  const errorLayerRef = useRef(null);
  const textareaRef = useRef(null);
  const [focused, setFocused] = useState(false);
  const [caret, setCaret] = useState(0);
  const [rawActiveIndex, setRawActiveIndex] = useState(0);
  const [dismissedAt, setDismissedAt] = useState(null);
  const [panelPos, setPanelPos] = useState(null);
  const highlighted = useMemo(() => {
    try {
      return hljs.highlight(value, { language: 'python' }).value;
    } catch {
      return value;
    }
  }, [value]);
  const issue = useMemo(() => checkExpressionSyntax(value), [value]);
  const errorHtml = useMemo(() => {
    if (issue.ok) return null;
    const before = escapeHtmlText(value.slice(0, issue.start));
    const flagged = escapeHtmlText(value.slice(issue.start, issue.end)) || ' ';
    const after = escapeHtmlText(value.slice(issue.end));
    return `${before}<span class="agents-graph-node__expr-error-span">${flagged}</span>${after}\n`;
  }, [issue, value]);

  const suggestions = useMemo(
    () => (autocomplete ? buildStatePathSuggestions(id, graph?.nodes, graph?.edges, graph?.inputSchema) : EMPTY_SUGGESTIONS),
    [autocomplete, id, graph?.nodes, graph?.edges, graph?.inputSchema],
  );
  const context = useMemo(
    () => (autocomplete && focused ? getExpressionAutocompleteContext(value, caret) : null),
    [autocomplete, focused, value, caret],
  );
  const filtered = useMemo(
    () => (context ? filterStatePathSuggestions(suggestions, context.query) : EMPTY_SUGGESTIONS),
    [suggestions, context],
  );
  const activeIndex = filtered.length ? ((rawActiveIndex % filtered.length) + filtered.length) % filtered.length : 0;
  const menuOpen = !!context && filtered.length > 0 && dismissedAt !== context.tokenStart;

  useLayoutEffect(() => {
    if (!menuOpen || !textareaRef.current) return;
    const rect = textareaRef.current.getBoundingClientRect();
    const coords = getCaretCoordinates(textareaRef.current, context.tokenStart);
    setPanelPos({
      top: rect.top - textareaRef.current.scrollTop + coords.top + coords.height + 4,
      left: rect.left - textareaRef.current.scrollLeft + coords.left,
    });
  }, [menuOpen, context?.tokenStart, value]);

  const onChange = (event) => {
    updateNodeConfig(id, { [field]: event.target.value });
    setCaret(event.target.selectionStart);
  };

  function accept(item) {
    if (!item || !context) return;
    const nextValue = `${value.slice(0, context.tokenStart)}${item.path}${value.slice(context.tokenEnd)}`;
    const nextCaret = context.tokenStart + item.path.length;
    updateNodeConfig(id, { [field]: nextValue });
    setDismissedAt(null);
    setRawActiveIndex(0);
    requestAnimationFrame(() => {
      const textarea = textareaRef.current;
      if (!textarea) return;
      textarea.focus();
      textarea.setSelectionRange(nextCaret, nextCaret);
      setCaret(nextCaret);
    });
  }

  function handleKeyDown(event) {
    if (!menuOpen) return;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setRawActiveIndex((current) => current + 1);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setRawActiveIndex((current) => current - 1);
    } else if (event.key === 'Enter') {
      // Tab deliberately does NOT accept here (unlike InputsField's old
      // line-based menu) -- with the menu now open for the whole time a
      // field is focused rather than only after typing a character, always
      // treating Tab as "accept" would trap normal keyboard navigation:
      // an author could no longer Tab out of an empty/finished expression
      // without first dismissing the menu.
      event.preventDefault();
      accept(filtered[activeIndex]);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      setDismissedAt(context.tokenStart);
    }
  }

  const syncCaret = (event) => setCaret(event.target.selectionStart);

  function syncScroll(event) {
    const { scrollTop, scrollLeft } = event.target;
    if (highlightRef.current) {
      highlightRef.current.scrollTop = scrollTop;
      highlightRef.current.scrollLeft = scrollLeft;
    }
    if (errorLayerRef.current) {
      errorLayerRef.current.scrollTop = scrollTop;
      errorLayerRef.current.scrollLeft = scrollLeft;
    }
  }
  return (
    <label className={`agents-graph-node__field${required ? ' agents-graph-node__field--required' : ''}`}>
      <span className="agents-graph-node__field-label">
        {label}
        {required ? <span className="agents-graph-node__required" title="Required before this routine can be saved">Required</span> : null}
      </span>
      <div
        className={`agents-graph-node__expr${required ? ' agents-graph-node__input--required' : ''}${issue.ok ? '' : ' agents-graph-node__expr--invalid'}`}
        title={issue.ok ? undefined : issue.message}
      >
        <pre ref={highlightRef} className="agents-graph-node__expr-highlight" aria-hidden="true">
          <code className="hljs language-python" dangerouslySetInnerHTML={{ __html: `${highlighted}\n` }} />
        </pre>
        {errorHtml ? (
          <pre ref={errorLayerRef} className="agents-graph-node__expr-error" aria-hidden="true" dangerouslySetInnerHTML={{ __html: errorHtml }} />
        ) : null}
        <textarea
          ref={textareaRef}
          className="agents-graph-node__expr-textarea"
          value={value}
          placeholder={placeholder}
          onChange={onChange}
          onScroll={syncScroll}
          onKeyDown={handleKeyDown}
          onKeyUp={syncCaret}
          onClick={syncCaret}
          onFocus={(event) => {
            setFocused(true);
            setDismissedAt(null);
            setCaret(event.target.selectionStart);
          }}
          onBlur={() => setFocused(false)}
          spellCheck={false}
          aria-required={required || undefined}
          aria-invalid={issue.ok ? undefined : true}
        />
      </div>
      {issue.ok ? null : <span className="agents-graph-node__expr-error-hint" role="alert">{issue.message}</span>}
      {menuOpen ? (
        <StatePathSuggestionsMenu items={filtered} activeIndex={activeIndex} position={panelPos} onHover={setRawActiveIndex} onSelect={accept} />
      ) : null}
    </label>
  );
}

// `agent_name` field for the `agent`/`a2a`/foreach-agent-mode node kinds: a
// searchable dropdown over the live agent roster (redux `agents.snapshot`)
// instead of a free-text box the author has to get exactly right by hand.
// `agentKind` scopes the roster to what that node kind can actually resolve
// at runtime -- `agent` nodes delegate to a local object agent
// (routines/delegation_nodes.py `_build_object_agent_node`), `a2a` nodes
// delegate to a registered Agent2Agent peer (`_build_a2a_node`) -- so the two must not
// share one option list. The current value is always kept in the list even
// if the live roster no longer has it (renamed/removed agent, or the
// snapshot just hasn't arrived yet) so a saved reference is never silently
// blanked out from under the author.
function AgentSelectField({ id, config, field = 'agent_name', label, agentKind, updateNodeConfig }) {
  const missing = useContext(MissingFieldsContext);
  const required = missing.has(field);
  const profiles = useSelector(selectAgentProfiles);
  const value = config?.[field] || '';
  const options = useMemo(() => {
    const names = profiles.filter((agent) => agent.kind === agentKind).map((agent) => agent.name).filter(Boolean);
    const known = new Set(names);
    if (value && !known.has(value)) names.push(value);
    return [...new Set(names)].sort((a, b) => a.localeCompare(b)).map((name) => ({ value: name, label: name }));
  }, [profiles, agentKind, value]);
  return (
    <label className={`agents-graph-node__field${required ? ' agents-graph-node__field--required' : ''}`}>
      <span className="agents-graph-node__field-label">
        {label}
        {required ? <span className="agents-graph-node__required" title="Required before this routine can be saved">Required</span> : null}
      </span>
      <Select
        value={value}
        onChange={(next) => updateNodeConfig(id, { [field]: next })}
        options={options}
        placeholder="Select an agent…"
        searchable
        portal
        ariaLabel={label}
        className={`agents-graph-node__select${required ? ' agents-graph-node__select--required' : ''}`}
      />
    </label>
  );
}

const EMPTY_SUGGESTIONS = [];

const MIRROR_STYLE_PROPS = [
  'boxSizing', 'width', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
  'borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth',
  'fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'letterSpacing', 'lineHeight',
  'textTransform', 'wordSpacing', 'tabSize', 'whiteSpace', 'wordWrap', 'overflowWrap',
];

// A <textarea> exposes no native API for "where on screen is character N" --
// the standard workaround (same technique as the "textarea-caret-position"
// pattern) is an offscreen div mirroring the textarea's exact box/font
// metrics, filled with the text up to `position`, whose trailing marker
// span's offset IS that position. Only invoked while the menu is open, not
// on every keystroke.
function getCaretCoordinates(textarea, position) {
  const div = document.createElement('div');
  const style = window.getComputedStyle(textarea);
  MIRROR_STYLE_PROPS.forEach((prop) => { div.style[prop] = style[prop]; });
  div.style.position = 'absolute';
  div.style.visibility = 'hidden';
  div.style.top = '0';
  div.style.left = '-9999px';
  div.style.whiteSpace = 'pre-wrap';
  div.style.wordWrap = 'break-word';
  div.style.overflowWrap = 'break-word';
  document.body.appendChild(div);
  div.textContent = textarea.value.slice(0, position);
  const marker = document.createElement('span');
  marker.textContent = textarea.value.slice(position) || '.';
  div.appendChild(marker);
  const coords = { top: marker.offsetTop, left: marker.offsetLeft, height: marker.offsetHeight || parseFloat(style.lineHeight) || 16 };
  document.body.removeChild(div);
  return coords;
}

// Caret-anchored, keeps focus in the textarea throughout (onMouseDown
// preventDefault on every option -- a real mousedown would blur the
// textarea before the click landed, closing the menu out from under it).
function StatePathSuggestionsMenu({ items, activeIndex, position, onHover, onSelect }) {
  if (!position) return null;
  return createPortal(
    <div className="app-listbox__panel agents-graph-node__suggest-menu" style={{ position: 'fixed', top: position.top, left: position.left }}>
      <ul className="app-listbox__list" role="listbox" aria-label="State path suggestions">
        {items.map((item, index) => (
          <li key={item.path} role="option" aria-selected={index === activeIndex} data-index={index}>
            <button
              type="button"
              className={`app-listbox__option ${index === activeIndex ? 'is-active' : ''}`}
              onMouseDown={(event) => event.preventDefault()}
              onMouseEnter={() => onHover(index)}
              onClick={() => onSelect(item)}
            >
              <span className="app-listbox__option-label">{item.path}</span>
              <span className="agents-graph-node__suggest-menu-source">{item.source}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>,
    document.body,
  );
}

function InputsField({ id, config, updateNodeConfig }) {
  const [text, setText, setTrackedInputs] = useBufferedFieldText(id, config?.inputs, inputsToText);
  return (
    <label className="agents-graph-node__field">
      <span className="agents-graph-node__field-label">Inputs (name: path, e.g. host: parse_json.host)</span>
      <textarea
        className="agents-graph-node__prompt"
        value={text}
        placeholder="host: parse_json.host"
        onChange={(event) => {
          const nextText = event.target.value;
          setText(nextText);
          const parsed = textToInputs(nextText);
          setTrackedInputs(parsed);
          updateNodeConfig(id, { inputs: parsed });
        }}
      />
    </label>
  );
}

function ResponseSchemaField({ id, config, field, label, updateNodeConfig }) {
  const [text, setText, setTrackedValue] = useBufferedFieldText(id, config?.[field], schemaFieldsToText);
  return (
    <label className="agents-graph-node__field">
      <span className="agents-graph-node__field-label">{label}</span>
      <textarea
        className="agents-graph-node__prompt"
        value={text}
        placeholder={'category: enum(billing, technical, other)\nsummary: string # one sentence recap of the request'}
        onChange={(event) => {
          const nextText = event.target.value;
          setText(nextText);
          const parsed = textToSchemaFields(nextText);
          setTrackedValue(parsed);
          updateNodeConfig(id, { [field]: parsed });
        }}
      />
    </label>
  );
}


function KeyValueMapField({ id, config, field, label, placeholder, updateNodeConfig }) {
  const [text, setText, setTrackedValue] = useBufferedFieldText(id, config?.[field], inputsToText);
  return (
    <label className="agents-graph-node__field">
      <span className="agents-graph-node__field-label">{label}</span>
      <textarea
        className="agents-graph-node__prompt"
        value={text}
        placeholder={placeholder}
        onChange={(event) => {
          const nextText = event.target.value;
          setText(nextText);
          const parsed = textToInputs(nextText);
          setTrackedValue(parsed);
          updateNodeConfig(id, { [field]: parsed });
        }}
      />
    </label>
  );
}


function StringListField({ id, config, field, label, placeholder, updateNodeConfig }) {
  return (
    <label className="agents-graph-node__field">
      <span className="agents-graph-node__field-label">{label}</span>
      <input
        className="agents-graph-node__toolinput"
        value={(config?.[field] || []).join(', ')}
        placeholder={placeholder}
        onChange={(event) => {
          const parsed = event.target.value.split(',').map((value) => value.trim()).filter(Boolean);
          updateNodeConfig(id, parsed.length ? { [field]: parsed } : { [field]: undefined });
        }}
      />
    </label>
  );
}


function NumberField({ id, config, field, label, placeholder, updateNodeConfig, min = 1 }) {
  return (
    <label className="agents-graph-node__field">
      <span className="agents-graph-node__field-label">{label}</span>
      <input
        type="number"
        min={min}
        className="agents-graph-node__toolinput"
        value={config?.[field] ?? ''}
        placeholder={placeholder}
        onChange={(event) => updateNodeConfig(id, {
          [field]: event.target.value === '' ? undefined : Number(event.target.value),
        })}
      />
    </label>
  );
}

// Kind-specific config fields, mirroring app.progenitor.routines.graph's per-kind
// config shape (prompt_template/system_prompt, expression, rubric_prompt/
// response_schema, agent_name + prompt_template/inputs). `noop` has no config.
export function NodeConfigBody({ id, kind, config, updateNodeConfig, replaceNodeConfig }) {
  if (kind === 'prompt') {
    return (
      <>
        <ModelProfileField id={id} config={config} updateNodeConfig={updateNodeConfig} />
        <ConfigField id={id} config={config} field="prompt_template" label="Prompt" placeholder="Template… use {request} / {other_node} to reference state" multiline updateNodeConfig={updateNodeConfig} />
        <ConfigField id={id} config={config} field="system_prompt" label="System prompt (optional)" placeholder="Node-specific system prompt…" multiline updateNodeConfig={updateNodeConfig} />
      </>
    );
  }
  if (kind === 'eval' || kind === 'condition' || kind === 'switch') {
    return (
      <ExpressionField id={id} config={config} field="expression" label="Expression" placeholder="Restricted Python expression…" updateNodeConfig={updateNodeConfig} autocomplete />
    );
  }
  if (kind === 'classify') {
    return (
      <>
        <ModelProfileField id={id} config={config} updateNodeConfig={updateNodeConfig} />
        <ConfigField id={id} config={config} field="rubric_prompt" label="Prompt" placeholder="e.g. Categorize this request: {request}" multiline updateNodeConfig={updateNodeConfig} />
        <ResponseSchemaField id={id} config={config} field="response_schema" label="Response fields (required)" updateNodeConfig={updateNodeConfig} />
      </>
    );
  }
  if (kind === 'agent') {
    return (
      <>
        <ModelProfileField id={id} config={config} updateNodeConfig={updateNodeConfig} />
        <AgentSelectField id={id} config={config} label="Agent" agentKind="object" updateNodeConfig={updateNodeConfig} />
        <label className="agents-graph-node__field">
          <span className="agents-graph-node__field-label">Execution</span>
          <select className="agents-graph-node__toolinput" aria-label="Execution"
            value={config?.execution || 'wait'}
            onChange={(event) => updateNodeConfig(id, { execution: event.target.value })}>
            <option value="wait">Wait for response</option>
            <option value="background">Run in background</option>
          </select>
        </label>
        <p className="agents-graph-node__field-label">
          {config?.execution === 'background'
            ? 'Continue after launch. Output contains the task reference; its result arrives separately. Each launch starts a separate conversation.'
            : 'Continue after the agent responds.'}
        </p>
        <ConfigField id={id} config={config} field="prompt_template" label="Prompt" placeholder="Template sent to the agent…" multiline updateNodeConfig={updateNodeConfig} />
        <InputsField id={id} config={config} updateNodeConfig={updateNodeConfig} />
      </>
    );
  }
  if (kind === 'a2a') {
    return (
      <>
        <AgentSelectField id={id} config={config} label="Agent" agentKind="a2a" updateNodeConfig={updateNodeConfig} />
        <ConfigField id={id} config={config} field="prompt_template" label="Prompt" placeholder="Template sent to the peer…" multiline updateNodeConfig={updateNodeConfig} />
      </>
    );
  }
  if (kind === 'human') {
    return (
      <>
        <ConfigField id={id} config={config} field="message" label="Approval message" placeholder="What should a person approve?" multiline updateNodeConfig={updateNodeConfig} />
        <StringListField id={id} config={config} field="allowed_responses" label="Allowed responses (comma-separated)" placeholder="approve, reject" updateNodeConfig={updateNodeConfig} />
      </>
    );
  }
  if (kind === 'foreach') {
    const mode = config?.mode || 'send';
    return (
      <>
        <label className="agents-graph-node__field">
          <span className="agents-graph-node__field-label">Execution mode</span>
          <select
            className="agents-graph-node__toolinput"
            value={mode}
            onChange={(event) => replaceNodeConfig(
              event.target.value === 'agent'
                ? { mode: 'agent', items_from: '', agent_name: '', prompt_template: '', item_key: 'item' }
                : { mode: 'send', items_from: '', item_key: 'item' },
            )}
          >
            <option value="send">Send to connected node</option>
            <option value="agent">Invoke named agent</option>
          </select>
        </label>
        <ConfigField id={id} config={config} field="items_from" label="Items from data path" placeholder="items" updateNodeConfig={updateNodeConfig} />
        <ConfigField id={id} config={config} field="item_key" label="Item state key" placeholder="item" updateNodeConfig={updateNodeConfig} />
        <NumberField id={id} config={config} field="max_items" label="Maximum items (optional)" placeholder="100" updateNodeConfig={updateNodeConfig} />
        {mode === 'agent' ? (
          <>
            <AgentSelectField id={id} config={config} label="Agent" agentKind="object" updateNodeConfig={updateNodeConfig} />
            <ConfigField id={id} config={config} field="prompt_template" label="Prompt per item" placeholder="Handle {item}" multiline updateNodeConfig={updateNodeConfig} />
            <NumberField id={id} config={config} field="max_concurrency" label="Maximum concurrency (optional)" placeholder="4" updateNodeConfig={updateNodeConfig} />
          </>
        ) : null}
      </>
    );
  }
  if (kind === 'routine') {
    return (
      <>
        <ConfigField id={id} config={config} field="routine_name" label="Routine name" placeholder="shared-validation-routine" updateNodeConfig={updateNodeConfig} />
        <KeyValueMapField id={id} config={config} field="input_map" label="Input map (child field: parent data path)" placeholder="request: request" updateNodeConfig={updateNodeConfig} />
        <KeyValueMapField id={id} config={config} field="output_map" label="Output map (parent field: child data path)" placeholder="result: answer" updateNodeConfig={updateNodeConfig} />
      </>
    );
  }
  if (kind === 'reduce') {
    return (
      <>
        <ConfigField id={id} config={config} field="source_node" label="Map output node ID" placeholder="e.g. transform" updateNodeConfig={updateNodeConfig} />
        <label className="agents-graph-node__field">
          <span className="agents-graph-node__field-label">Operation</span>
          <select
            className="agents-graph-node__toolinput"
            value={config?.operation || 'collect'}
            onChange={(event) => updateNodeConfig(id, { operation: event.target.value })}
          >
            <option value="collect">Collect values</option>
            <option value="sum">Sum</option>
            <option value="product">Product</option>
            <option value="count">Count</option>
            <option value="min">Minimum</option>
            <option value="max">Maximum</option>
          </select>
        </label>
        <p className="routine-node-inspector__hint">Collects each execution of that node, such as the final step in a Send foreach body. Empty input reduces to an empty list, zero for sum/count, one for product, and null for min/max.</p>
      </>
    );
  }
  return null;
}
