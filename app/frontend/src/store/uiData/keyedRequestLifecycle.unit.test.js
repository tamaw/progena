import { describe, expect, it } from 'vitest';
import reducer, { applyUiEvent } from '../uiDataSlice';
import { fetchMcpServerDetail, selectMcpServerDetailRequest } from './mcp';
import { fetchInstruction, selectInstructionContentRequest } from './instructions';
import { fetchWorkflowContent, selectWorkflowDetailRequest } from './workflows';

const uiState = (state) => ({ uiData: state });

describe('keyed detail request lifecycle', () => {
  it('recovers a rejected cache miss when its matching data event arrives', () => {
    const args = { id: 'mcp:a' };
    let state = reducer(undefined, fetchMcpServerDetail.pending('mcp-a', args));
    state = reducer(state, fetchMcpServerDetail.rejected(new Error('offline'), 'mcp-a', args));
    expect(selectMcpServerDetailRequest(uiState(state), 'mcp:a')).toMatchObject({
      loading: false,
      error: { message: 'offline' },
      requestId: 'mcp-a',
    });

    state = reducer(state, applyUiEvent({
      type: 'mcp.server.detail.updated',
      payload: { id: 'mcp:a', request_id: 'mcp-a' },
    }));

    expect(selectMcpServerDetailRequest(uiState(state), 'mcp:a')).toEqual({
      loading: false, error: null, requestId: null,
    });
    expect(selectMcpServerDetailRequest(uiState(state), 'mcp:b')).toEqual({
      loading: false, error: null, requestId: null,
    });
  });

  it('clears cancelled loading without presenting a failure', () => {
    const args = { id: 'mcp:a' };
    let state = reducer(undefined, fetchMcpServerDetail.pending('cancelled-request', args));
    state = reducer(state, fetchMcpServerDetail.rejected(
      { name: 'AbortError', message: 'Aborted' }, 'cancelled-request', args,
    ));
    expect(selectMcpServerDetailRequest(uiState(state), 'mcp:a')).toEqual({
      loading: false, error: null, requestId: null,
    });
  });

  it('ignores an older failed request event until the replacement request matches', () => {
    const args = { id: 'mcp:a' };
    let state = reducer(undefined, fetchMcpServerDetail.pending('first-request', args));
    state = reducer(state, fetchMcpServerDetail.rejected(
      new Error('first failed'), 'first-request', args,
    ));
    state = reducer(state, fetchMcpServerDetail.pending('second-request', args));
    state = reducer(state, fetchMcpServerDetail.rejected(
      new Error('second failed'), 'second-request', args,
    ));

    state = reducer(state, applyUiEvent({
      type: 'mcp.server.detail.updated',
      payload: { id: 'mcp:a', request_id: 'first-request' },
    }));

    expect(selectMcpServerDetailRequest(uiState(state), 'mcp:a')).toMatchObject({
      loading: false,
      error: { message: 'second failed' },
      requestId: 'second-request',
    });

    state = reducer(state, applyUiEvent({
      type: 'mcp.server.detail.updated',
      payload: { id: 'mcp:a', request_id: 'second-request' },
    }));

    expect(selectMcpServerDetailRequest(uiState(state), 'mcp:a')).toEqual({
      loading: false, error: null, requestId: null,
    });
  });

  it('requires the matching data event and tolerates event-before-ack delivery', () => {
    const args = { path: 'system.md' };
    let state = reducer(undefined, fetchInstruction.pending('instruction-a', args));
    state = reducer(state, applyUiEvent({
      type: 'instruction.loaded',
      payload: { path: 'system.md', content: '# System', request_id: 'instruction-a' },
    }));
    state = reducer(state, fetchInstruction.fulfilled({ request_id: 'instruction-a' }, 'instruction-a', args));

    expect(selectInstructionContentRequest(uiState(state), 'system.md')).toEqual({
      loading: false,
      error: null,
      requestId: null,
    });
  });

  it('does not let a late error or unrelated event poison another detail key', () => {
    const first = { path: '/workflows/first.json' };
    const second = { path: '/workflows/second.json' };
    let state = reducer(undefined, fetchWorkflowContent.pending('first-request', first));
    state = reducer(state, fetchWorkflowContent.pending('second-request', second));
    state = reducer(state, applyUiEvent({
      type: 'workflow.updated',
      payload: {
        path: second.path,
        request_id: 'other-request',
        workflow: { id: 'second', title: 'Second', graph: { nodes: {} } },
      },
    }));
    state = reducer(state, fetchWorkflowContent.rejected(new Error('first failed'), 'first-request', first));
    expect(selectWorkflowDetailRequest(uiState(state), first.path)).toMatchObject({
      loading: false,
      error: { message: 'first failed' },
      requestId: 'first-request',
    });
    expect(selectWorkflowDetailRequest(uiState(state), second.path)).toEqual({
      loading: true,
      error: null,
      requestId: 'second-request',
    });

    state = reducer(state, applyUiEvent({
      type: 'workflow.updated',
      payload: {
        path: second.path,
        request_id: 'second-request',
        workflow: { id: 'second', title: 'Second', graph: { nodes: {} } },
      },
    }));
    state = reducer(state, fetchWorkflowContent.rejected(new Error('late'), 'second-request', second));

    expect(selectWorkflowDetailRequest(uiState(state), second.path)).toEqual({
      loading: false,
      error: null,
      requestId: null,
    });
  });
});
