

export const selectUiStatus = (state) => state.uiData.status;

export const initialState = {
  status: null
};

export const eventHandlers = {
  'connection.ready': (state, payload) => {
    if (payload.status) {
      state.status = payload.status;
    }
  },
  'status.updated': (state, payload) => {
    // Merge rather than replace so a partial push (e.g. the periodic
    // process-metrics tick) updates only the fields it carries and leaves the
    // rest (agents/tools/schedule/...) intact.
    state.status = {
      ...state.status,
      ...payload
    };
  }
};
