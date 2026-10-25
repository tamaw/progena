import { describe, expect, it } from 'vitest';
import {
  buildToolCallView,
  parseJavascriptOutput,
  parsePathList,
  parseReadFileOutput,
  parseToolArgs,
} from '@frontend/features/chat/models/toolViewModel';

const success = (toolName, argsPreview, resultText) => buildToolCallView({ toolName, argsPreview, resultText, status: 'success' });

describe('read_file output', () => {
  it('lifts the status header into the meta and numbers lines from the window start', () => {
    const view = parseReadFileOutput('@@ lines 11-12 of 40 | next offset 12 @@\nalpha\nbeta');
    expect(view.meta).toBe('lines 11-12 of 40 · next offset 12');
    expect(view.body).toEqual({ kind: 'code', text: 'alpha\nbeta', startLine: 11 });
  });

  it('reads older cat -n output and marks a cut-off preview', () => {
    const view = parseReadFileOutput('     5\tone\n     6\ttwo…');
    expect(view.body).toEqual({ kind: 'code', text: 'one\ntwo', startLine: 5 });
    expect(view.meta).toBe('lines 5–6 · preview');
  });
});

describe('ls / glob output', () => {
  it('parses the python list repr, counting items only when the list is complete', () => {
    expect(parsePathList("['/a/b.json', \"/c's\"]")).toEqual(['/a/b.json', "/c's"]);
    expect(success('ls', { path: '/x' }, "['/x/1', '/x/2']").output.meta).toBe('2 items');
    expect(success('glob', { pattern: '*' }, "['/x/1', '/x/2', '/x/3…").output.meta).toBe('2+ items · preview');
    expect(success('ls', { path: '/x' }, '[]').output).toEqual({ label: 'Output', meta: 'empty', tone: '', body: null });
  });
});

describe('edit_file / write_file', () => {
  it('shows an edit as a diff and its confirmation as a count with no body', () => {
    const view = success('edit_file', { file_path: '/f', old_string: 'a', new_string: 'b\nc', replace_all: false }, "Successfully replaced 3 instance(s) of the string in '/f'");
    expect(view.input.meta).toBe('−1 +2');
    expect(view.input.body.rows).toEqual([{ name: 'file_path', value: '/f' }]);
    expect(view.input.body.block).toEqual({ kind: 'diff', removed: 'a', added: 'b\nc' });
    expect(view.output).toMatchObject({ meta: 'replaced 3', body: null });
  });

  it('keeps an unexpected write_file result visible instead of hiding it', () => {
    const view = success('write_file', { file_path: '/f', content: 'x' }, 'Error: permission denied');
    expect(view.output.body).toEqual({ kind: 'text', text: 'Error: permission denied' });
  });
});

describe('artifact output summaries', () => {
  it('keeps the authoritative omitted-line summary alongside the useful preview', () => {
    const view = buildToolCallView({
      toolName: 'read_file',
      argsPreview: { path: '/src/main.py' },
      resultText: 'line 1\nline 2\nline 3',
      artifact: { summary: 'src/main.py: 5 lines · 2 more lines not shown' },
      status: 'success',
    });

    expect(view.output.meta).toContain('src/main.py: 5 lines · 2 more lines not shown');
    expect(view.output.body).toEqual({ kind: 'text', text: 'line 1\nline 2\nline 3' });
  });

});

describe('execute output', () => {
  it('turns the exit status line into the meta and a failed command into an Error box', () => {
    const view = success('execute', { command: 'false' }, 'boom\n[Command failed with exit code 2]');
    expect(view.input.body).toEqual({ kind: 'command', text: 'false' });
    expect(view.output).toMatchObject({ label: 'Error', meta: 'exit 2', tone: 'error', body: { text: 'boom' } });
  });
});

describe('javascript output', () => {
  it('unwraps stdout and result, unescaping XML entities', () => {
    const view = parseJavascriptOutput('<stdout>\nlog &lt;1&gt;\n</stdout>\n<result kind="json">{"a":1}</result>');
    expect(view).toMatchObject({ label: 'Output', meta: 'json', body: { text: 'log <1>\n\n{"a":1}' } });
  });

  it('reports a thrown error as an Error box typed by the error', () => {
    const view = parseJavascriptOutput('<error type="TypeError">x is undefined</error>');
    expect(view).toMatchObject({ label: 'Error', meta: 'TypeError', tone: 'error', body: { text: 'x is undefined' } });
  });

  it('tolerates a preview cut off before the closing tag', () => {
    expect(parseJavascriptOutput('<result>partial out').body.text).toBe('partial out');
  });
});

describe('inputs', () => {
  it('shows a live preview cut off into a string as raw text', () => {
    expect(parseToolArgs('{"content": "abc')).toBeNull();
    const view = success('write_file', '{"content": "abc', 'Updated file /f');
    expect(view.input).toMatchObject({ meta: 'preview', body: { kind: 'text', text: '{"content": "abc' } });
  });

  it('gives other tools short fields as rows and their longest value as a block', () => {
    const view = success('acp_send', { task_id: 't1', message: 'x'.repeat(200) }, '{"ok": true}');
    expect(view.input.body.rows).toEqual([{ name: 'task_id', value: 't1' }]);
    expect(view.input.body.block).toMatchObject({ kind: 'code', name: 'message' });
    expect(view.output.body).toEqual({ kind: 'text', text: '{"ok": true}' });
  });

  it('labels a failed call Error with its error text', () => {
    const view = buildToolCallView({ toolName: 'ls', argsPreview: { path: '/' }, errorText: 'denied', status: 'error' });
    expect(view.output).toMatchObject({ label: 'Error', tone: 'error', body: { text: 'denied' } });
  });
});
