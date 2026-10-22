import { describe, it, expect } from 'vitest';
import { derivePageContext, formatPageContextBlock, stripPageContextBlock } from '@frontend/lib/pageContext';

describe('derivePageContext', () => {
  it('extracts the id as the first segment after the base path', () => {
    expect(derivePageContext('/inbox/msg_123')).toEqual({ page: 'inbox', id: 'msg_123' });
    expect(derivePageContext('/inbox/msg_123/some-title-slug')).toEqual({ page: 'inbox', id: 'msg_123' });
    expect(derivePageContext('/tasks/card_42')).toEqual({ page: 'tasks', id: 'card_42' });
    expect(derivePageContext('/schedules/sched_1')).toEqual({ page: 'schedules', id: 'sched_1' });
    expect(derivePageContext('/knowledge/kb_1')).toEqual({ page: 'knowledge', id: 'kb_1' });
    expect(derivePageContext('/tools/server_1')).toEqual({ page: 'tools', id: 'server_1' });
    expect(derivePageContext('/agents/agent_1/settings')).toEqual({ page: 'agents', id: 'agent_1' });
    expect(derivePageContext('/routines/agent_1')).toEqual({ page: 'routines', id: 'agent_1' });
    expect(derivePageContext('/chat/thread_1/new-chat')).toEqual({ page: 'chat', id: 'thread_1' });
  });

  it('returns a null id for a bare base path or an id-less page', () => {
    expect(derivePageContext('/inbox')).toEqual({ page: 'inbox', id: null });
    expect(derivePageContext('/dashboard')).toEqual({ page: 'dashboard', id: null });
    expect(derivePageContext('/workflows')).toEqual({ page: 'workflows', id: null });
    expect(derivePageContext('/channels')).toEqual({ page: 'channels', id: null });
    expect(derivePageContext('/repositories')).toEqual({ page: 'repositories', id: null });
  });

  it('reads the plugin id from the second segment, not the marketplace', () => {
    expect(derivePageContext('/plugins/acme/some-plugin')).toEqual({ page: 'plugins', id: 'some-plugin' });
    expect(derivePageContext('/plugins/acme')).toEqual({ page: 'plugins', id: null });
    expect(derivePageContext('/plugins')).toEqual({ page: 'plugins', id: null });
  });

  it('decodes a url-encoded id segment', () => {
    expect(derivePageContext('/inbox/msg%20123')).toEqual({ page: 'inbox', id: 'msg 123' });
  });

  it('returns null for an unmatched path', () => {
    expect(derivePageContext('/models')).toBeNull();
    expect(derivePageContext('/')).toBeNull();
    expect(derivePageContext('')).toBeNull();
  });
});

describe('formatPageContextBlock', () => {
  it('renders a fenced JSON block with the id', () => {
    expect(formatPageContextBlock({ page: 'inbox', id: 'msg_123' }))
      .toBe('```page-context\n{"page":"inbox","id":"msg_123"}\n```');
  });

  it('omits id from the payload when null', () => {
    expect(formatPageContextBlock({ page: 'dashboard', id: null }))
      .toBe('```page-context\n{"page":"dashboard"}\n```');
  });
});

describe('stripPageContextBlock', () => {
  it('removes a leading page-context block, leaving the rest untouched', () => {
    const sent = `${formatPageContextBlock({ page: 'inbox', id: 'msg_1' })}\n\nApprove this.`;
    expect(stripPageContextBlock(sent)).toBe('Approve this.');
  });

  it('leaves content without a block unchanged', () => {
    expect(stripPageContextBlock('Approve this.')).toBe('Approve this.');
  });

  it('only strips a block anchored at the start of the string', () => {
    const notAtStart = `Approve this.\n\n${formatPageContextBlock({ page: 'inbox', id: 'msg_1' })}`;
    expect(stripPageContextBlock(notAtStart)).toBe(notAtStart);
  });

  it('passes through non-string content unchanged', () => {
    expect(stripPageContextBlock(null)).toBeNull();
    expect(stripPageContextBlock(undefined)).toBeUndefined();
  });
});
