// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DetailHeader, { DetailHeaderAction, DetailHeaderActions } from '@frontend/components/layout/DetailHeader';
import EditableDetailTitle from '@frontend/components/EditableDetailTitle';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let root;
let container;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const render = (element) => act(() => root.render(element));
const longTitle = 'A very long workflow title that must truncate instead of pushing the actions';

describe('DetailHeader titles', () => {
  it('wraps plain titles in the shared truncating text node and keeps the full accessible name', () => {
    render(<DetailHeader title={longTitle} />);
    const heading = container.querySelector('h1.detail-header__title');
    expect(heading.querySelector('.detail-header__title-text').textContent).toBe(longTitle);
    expect(heading.textContent).toBe(longTitle);
  });

  it('leaves composed titles to render their own text node', () => {
    render(<DetailHeader title={<input aria-label="Agent name" defaultValue="Draft" />} />);
    expect(container.querySelector('.detail-header__title-text')).toBeNull();
    expect(container.querySelector('input[aria-label="Agent name"]')).not.toBeNull();
  });

  it('renders editable titles with the feature-neutral row and rename control', () => {
    const editor = { isEditing: false, draft: '', saving: false, error: null, setDraft: vi.fn(), start: vi.fn(), cancel: vi.fn(), save: vi.fn() };
    render(
      <DetailHeader
        titleClassName="detail-header__title--editable"
        title={<EditableDetailTitle title={longTitle} editor={editor} maxLength={120} ariaLabel="Schedule name" entityLabel="schedule" />}
      />,
    );
    const heading = container.querySelector('h1.detail-header__title--editable');
    expect(heading.querySelector('.detail-header__title-text').textContent).toBe(longTitle);
    const rename = heading.querySelector('button[aria-label="Rename schedule"]');
    act(() => rename.click());
    expect(editor.start).toHaveBeenCalledTimes(1);
  });

  it('keeps the editable input length limit and accessible name while editing', () => {
    const editor = { isEditing: true, draft: 'Draft', saving: false, error: null, setDraft: vi.fn(), start: vi.fn(), cancel: vi.fn(), save: vi.fn() };
    render(<DetailHeader title={<EditableDetailTitle title="x" editor={editor} maxLength={120} ariaLabel="Schedule name" entityLabel="schedule" />} />);
    const input = container.querySelector('input[aria-label="Schedule name"]');
    expect(input.maxLength).toBe(120);
  });
});

describe('DetailHeaderActions overflow parity', () => {
  it('derives overflow menu items from the inline actions with the same order, toggle and disabled state', () => {
    const onRun = vi.fn();
    const onDelete = vi.fn();
    render(
      <DetailHeader
        title="Workflow"
        actions={(
          <DetailHeaderActions menuLabel="Workflow actions">
            <DetailHeaderAction label="Run" ariaLabel="Run workflow" onClick={onRun} />
            <DetailHeaderAction label="Pin" aria-pressed onClick={() => {}} />
            <DetailHeaderAction label="Save" disabled onClick={() => {}} />
            <DetailHeaderAction label="Delete" onClick={onDelete} />
          </DetailHeaderActions>
        )}
      />,
    );
    const inline = [...container.querySelectorAll('.detail-header-actions__inline > button')];
    expect(inline.map((button) => button.getAttribute('aria-label'))).toEqual(['Run workflow', 'Pin', 'Save', 'Delete']);
    expect(inline.every((button) => button.className.includes('detail-header-action--default'))).toBe(true);

    act(() => container.querySelector('button[aria-label="Workflow actions"]').click());
    const items = [...container.querySelectorAll('[role="menu"] button')];
    expect(items.map((item) => item.textContent)).toEqual(['Run', 'Pin', 'Save', 'Delete']);
    expect(items[1].getAttribute('role')).toBe('menuitemcheckbox');
    expect(items[1].getAttribute('aria-checked')).toBe('true');
    expect(items[2].disabled).toBe(true);
    expect(items.some((item) => item.className.includes('is-danger'))).toBe(false);

    act(() => items[3].click());
    expect(onDelete).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[role="menu"]')).toBeNull();
  });
});
