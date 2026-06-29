/**
 * Shared JSON fetch helpers for Redux thunks.
 *
 * Centralizes the request boilerplate that every uiData thunk used to repeat:
 * Accept/Content-Type headers, JSON body serialization, `response.ok` checks,
 * empty-body handling, and error messages built from a label plus any `detail`
 * field on a JSON error body.
 */

/**
 * Perform a fetch and return the parsed JSON body (or `null` for empty/204
 * responses). Throws an Error of the form `"<errorLabel> <detail>"` when the
 * response is not ok, where `detail` is the server's `detail` field when present
 * or `(<status>)` otherwise.
 *
 * @param {string} url
 * @param {{ method?: string, body?: unknown, errorLabel?: string }} [options]
 */
export async function requestJson(url, { method = 'GET', body, errorLabel = 'Request failed' } = {}) {
  const headers = { Accept: 'application/json' };
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
  }

  const response = await fetch(url, {
    method,
    headers,
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });

  const text = await response.text();

  if (!response.ok) {
    let detail = `(${response.status})`;
    if (text) {
      try {
        const errorBody = JSON.parse(text);
        if (errorBody?.detail) {
          detail = typeof errorBody.detail === 'string'
            ? errorBody.detail
            : JSON.stringify(errorBody.detail);
        }
      } catch {
        // non-JSON error body; keep the status-code detail
      }
    }
    throw new Error(`${errorLabel} ${detail}`);
  }

  return text ? JSON.parse(text) : null;
}

/**
 * Append query params to a URL, choosing `?` or `&` based on the existing URL.
 * Returns the original URL unchanged when there are no params.
 *
 * @param {string} url
 * @param {Record<string, string>} params
 */
export function withQuery(url, params) {
  const search = new URLSearchParams(params).toString();
  if (!search) {
    return url;
  }
  return `${url}${url.includes('?') ? '&' : '?'}${search}`;
}
