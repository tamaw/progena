import { useCallback, useEffect, useMemo, useState } from 'react';
import Select from '../../components/Select';
import NumberStepper from '../../components/NumberStepper';
import { DetailHeaderAction, DetailStatus } from '../../components/layout/DetailHeader';
import { DashboardNavIcon, InboxNavIcon, RailSettingsIcon, RefreshIcon, RoutinesNavIcon } from '../../components/navIcons';
import DesignLibraryPatterns from './DesignLibraryPatterns';
import './DesignLibraryPage.css';

const THEME_TOKENS = [
  '--color-bg-primary',
  '--color-bg-secondary',
  '--color-bg-tertiary',
  '--color-bg-hover',
  '--color-text-primary',
  '--color-text-secondary',
  '--color-text-muted',
  '--color-border',
  '--color-accent',
  '--color-success',
  '--color-warning',
  '--color-error',
];

const TOKEN_LABELS = {
  '--color-bg-primary': 'Primary canvas',
  '--color-bg-secondary': 'Raised surface',
  '--color-bg-tertiary': 'Inset surface',
  '--color-bg-hover': 'Hover surface',
  '--color-text-primary': 'Primary text',
  '--color-text-secondary': 'Secondary text',
  '--color-text-muted': 'Muted text',
  '--color-border': 'Border',
  '--color-accent': 'Accent',
  '--color-success': 'Success',
  '--color-warning': 'Warning',
  '--color-error': 'Error',
};

const SELECT_OPTIONS = [
  { value: 'balanced', label: 'Balanced' },
  { value: 'focused', label: 'Focused' },
  { value: 'creative', label: 'Creative' },
  { value: 'precise', label: 'Precise' },
];

function readThemeTokens() {
  const styles = window.getComputedStyle(document.documentElement);
  return Object.fromEntries(THEME_TOKENS.map((name) => [name, styles.getPropertyValue(name).trim()]));
}

function SourceNote({ children, kind = 'Markup pattern' }) {
  return (
    <p className="dl-source-note">
      <span className="dl-source-note__kind">{kind}</span>
      <code>{children}</code>
    </p>
  );
}

function SectionHeading({ eyebrow, title, titleId, children }) {
  return (
    <header className="dl-section-heading">
      <p className="dl-eyebrow">{eyebrow}</p>
      <h2 id={titleId}>{title}</h2>
      {children ? <p>{children}</p> : null}
    </header>
  );
}

export default function DesignLibraryPage() {
  const [tokens, setTokens] = useState(() => readThemeTokens());
  const [selectValue, setSelectValue] = useState('balanced');
  const [stepperValue, setStepperValue] = useState(3);
  const [form, setForm] = useState({ name: 'Research brief', path: '', note: '', notify: true, compact: false, mode: 'review', confidence: 65 });
  const [buttonCount, setButtonCount] = useState(0);

  const refreshTokens = useCallback(() => setTokens(readThemeTokens()), []);

  useEffect(() => {
    const observer = new MutationObserver(refreshTokens);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'style', 'class'] });
    return () => observer.disconnect();
  }, [refreshTokens]);

  const tokenCards = useMemo(() => THEME_TOKENS.map((name) => ({ name, value: tokens[name] || '—' })), [tokens]);

  return (
    <section className="design-library" aria-label="Progenitor design library">
      <div className="dl-page">
        <nav className="dl-index" aria-label="Catalogue sections">
          <a href="#library-foundations">Foundations</a>
          <a href="#library-controls">Controls</a>
          <a href="#library-navigation">Navigation</a>
          <a href="#library-layouts">Layouts</a>
          <a href="#library-feedback">Feedback</a>
          <a href="#library-data">Data</a>
        </nav>

        <section id="library-foundations" className="dl-section" aria-labelledby="library-foundations-title">
          <SectionHeading eyebrow="01 · Foundations" title="The current visual language" titleId="library-foundations-title">
            Values below are read from the active document theme, so switching the app theme updates the rendered token values rather than showing a static swatch.
          </SectionHeading>

          <div className="dl-foundation-grid">
            <article className="dl-card dl-card--wide">
              <h3>Theme tokens</h3>
              <div className="dl-token-grid">
                {tokenCards.map(({ name, value }) => (
                  <div className="dl-token" key={name}>
                    <span className="dl-token__swatch" style={{ backgroundColor: `var(${name})` }} aria-hidden="true" />
                    <div>
                      <strong>{TOKEN_LABELS[name]}</strong>
                      <code>{name}</code>
                      <span>{value}</span>
                    </div>
                  </div>
                ))}
              </div>
              <SourceNote kind="Current stylesheet">styles/main.css</SourceNote>
            </article>

            <article className="dl-card">
              <h3>Type &amp; chrome</h3>
              <div className="dl-type-sample">
                <p className="dl-type-sample__inter">Inter is the application face.</p>
                <p className="dl-type-sample__mono">JetBrains Mono is used for paths, metadata, and code.</p>
                <p className="dl-type-sample__hand">Shantell Sans appears in the chat writing block.</p>
              </div>
              <div className="dl-brand-gallery" aria-label="Current Progenitor branding and navigation icons">
                <span className="app-shell__brand-mark" aria-hidden="true">🧬</span>
                <strong className="app-shell__brand-name">Progenitor</strong>
                <span className="dl-icon-gallery" aria-label="Navigation icon samples">
                  <DashboardNavIcon /><InboxNavIcon /><RoutinesNavIcon />
                </span>
              </div>
              <SourceNote kind="Current chrome">shell/AppRuntimeShell.jsx · components/navIcons.jsx</SourceNote>
            </article>

            <article className="dl-card">
              <h3>Spacing &amp; radii</h3>
              <div className="dl-scale" aria-label="Spacing scale">
                {[['xs', '4px'], ['sm', '8px'], ['md', '12px'], ['lg', '18px'], ['xl', '28px']].map(([name, value]) => (
                  <div className="dl-scale__item" key={name}><span style={{ width: `var(--spacing-${name})` }} /><code>--spacing-{name}</code><em>{value}</em></div>
                ))}
              </div>
              <div className="dl-radius-row" aria-label="Radius scale">
                {['sm', 'md', 'lg', 'xl'].map((size) => <span key={size} style={{ borderRadius: `var(--border-radius-${size})` }}>{size}</span>)}
              </div>
              <SourceNote kind="Current stylesheet">styles/main.css</SourceNote>
            </article>
          </div>
        </section>

        <section id="library-controls" className="dl-section" aria-labelledby="library-controls-title">
          <SectionHeading eyebrow="02 · Controls" title="Shared controls and existing form patterns" titleId="library-controls-title">
            Controls marked “shared component” render existing exports. Other examples use the app’s existing CSS classes and native controls, without creating another component system.
          </SectionHeading>

          <div className="dl-control-grid">
            <article className="dl-card">
              <h3>Buttons &amp; actions</h3>
              <div className="dl-button-row">
                <button type="button" className="task-btn task-btn--primary" onClick={() => setButtonCount((count) => count + 1)}>Primary{buttonCount ? ` · ${buttonCount}` : ''}</button>
                <button type="button" className="task-btn task-btn--secondary" onClick={() => setButtonCount((count) => count + 1)}>Secondary</button>
                <DetailHeaderAction icon={<RailSettingsIcon />} label="Configure catalogue" ariaLabel="Configure catalogue" onClick={() => setButtonCount((count) => count + 1)} />
                <DetailHeaderAction icon={<RefreshIcon />} label="Reset counter" ariaLabel="Reset counter" onClick={() => setButtonCount(0)} />
                <button type="button" className="task-btn task-btn--secondary" disabled>Disabled</button>
              </div>
              <SourceNote kind="Existing markup &amp; shared component">features/tasks/kanban.css · components/layout/DetailHeader.jsx</SourceNote>
            </article>

            <article className="dl-card">
              <h3>Custom select</h3>
              <Select value={selectValue} onChange={setSelectValue} options={SELECT_OPTIONS} searchable ariaLabel="Response style" />
              <p className="dl-inline-note">Try typing, <kbd>↑</kbd> <kbd>↓</kbd>, <kbd>Enter</kbd>, or <kbd>Esc</kbd>.</p>
              <SourceNote kind="Shared component">components/Select.jsx</SourceNote>
            </article>

            <article className="dl-card">
              <h3>Number stepper</h3>
              <NumberStepper value={stepperValue} min={1} max={6} step={1} onChange={setStepperValue} ariaLabel="Parallel tasks" />
              <span className="dl-inline-value">{stepperValue} parallel tasks</span>
              <SourceNote kind="Shared component">components/NumberStepper.jsx</SourceNote>
            </article>
          </div>

          <div className="dl-field-grid">
            <article className="dl-card dl-card--form">
              <h3>Existing field treatment</h3>
              <label className="dl-field">
                <span>Work item title</span>
                <input className="set-text-input" value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} aria-describedby="library-title-help" />
                <small id="library-title-help">Concise, user-facing task name.</small>
              </label>
              <label className={`dl-field ${form.path ? '' : 'dl-field--error'}`}>
                <span>Required path</span>
                <input className="set-text-input" value={form.path} onChange={(event) => setForm((current) => ({ ...current, path: event.target.value }))} placeholder="src/features/…" aria-invalid={!form.path} aria-describedby="library-path-help" />
                <small id="library-path-help">{form.path ? 'Path captured locally.' : 'Choose a path before saving.'}</small>
              </label>
              <label className="dl-field">
                <span>Implementation note</span>
                <textarea value={form.note} onChange={(event) => setForm((current) => ({ ...current, note: event.target.value }))} placeholder="Local-only demo state is safe to change." rows="3" aria-describedby="library-note-help" />
                <small id="library-note-help">Textarea uses the app’s native form baseline.</small>
              </label>
              <label className="dl-field">
                <span>Unavailable field</span>
                <input className="set-text-input" value="Managed by the runtime" disabled readOnly />
              </label>
              <SourceNote kind="Existing markup">styles/chrome-frame.css (.set-text-input)</SourceNote>
            </article>

            <article className="dl-card dl-card--form">
              <h3>Native choices &amp; switch</h3>
              <label className="dl-check">
                <input type="checkbox" checked={form.notify} onChange={(event) => setForm((current) => ({ ...current, notify: event.target.checked }))} />
                <span>Notify when complete</span>
              </label>
              <div className="dl-switch-row">
                <span>Compact activity</span>
                <label className={`set-switch ${form.compact ? 'is-on' : ''}`}>
                  <input type="checkbox" checked={form.compact} onChange={(event) => setForm((current) => ({ ...current, compact: event.target.checked }))} aria-label="Compact activity" />
                  <span className="set-switch__knob" aria-hidden="true" />
                </label>
              </div>
              <fieldset className="dl-radio-group">
                <legend>Review mode</legend>
                <label><input type="radio" name="library-mode" value="review" checked={form.mode === 'review'} onChange={(event) => setForm((current) => ({ ...current, mode: event.target.value }))} /> Review</label>
                <label><input type="radio" name="library-mode" value="apply" checked={form.mode === 'apply'} onChange={(event) => setForm((current) => ({ ...current, mode: event.target.value }))} /> Apply</label>
              </fieldset>
              <label className="dl-range">
                <span>Confidence <output>{form.confidence}%</output></span>
                <input type="range" aria-label="Confidence" min="0" max="100" value={form.confidence} onChange={(event) => setForm((current) => ({ ...current, confidence: Number(event.target.value) }))} />
              </label>
              <label className="dl-check dl-check--disabled"><input type="checkbox" disabled /> Disabled choice</label>
              <SourceNote kind="Existing markup">styles/chrome-frame.css (.set-switch) · styles/main.css</SourceNote>
            </article>

            <article className="dl-card">
              <h3>Small affordances</h3>
              <div className="dl-affordance-list">
                <p><a href="#library-foundations">In-page link</a> keeps the accent treatment familiar.</p>
                <p>Shortcut hint <kbd>⌘</kbd> <kbd>K</kbd></p>
                <DetailHeaderAction icon={<RailSettingsIcon />} label="Configure" ariaLabel="Configure catalogue" onClick={() => setButtonCount((count) => count + 1)} />
              </div>
              <div className="dl-badge-row" aria-label="Status badge examples">
                <DetailStatus tone="ok">Ready</DetailStatus>
                <DetailStatus tone="warning">Queued</DetailStatus>
                <DetailStatus tone="error">Needs attention</DetailStatus>
              </div>
              <SourceNote kind="Shared components">components/layout/DetailHeader.jsx</SourceNote>
            </article>
          </div>
        </section>

        <DesignLibraryPatterns />

      </div>
    </section>
  );
}
