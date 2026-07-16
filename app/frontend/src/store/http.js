import { createUuid } from '../lib/uuid';
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Return the caller-provided UUID when it is valid, otherwise create the UUID
 * used as the operation ID for a finite UI request.
 */
export function createOperationRequestId({ requestId } = {}) {
  return typeof requestId === 'string' && UUID_PATTERN.test(requestId)
    ? requestId
    : createUuid();
}

export function isOperationRequestId(requestId) {
  return typeof requestId === 'string' && UUID_PATTERN.test(requestId);
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

/**
 * Submit a finite browser request and validate its 202 acknowledgement.
 *
 * The caller provides a *requestId* (UUID); it is sent as the X-Request-ID
 * header. The acknowledgement contains only its matching request ID and the
 * causal UI event types. Domain data and terminal state arrive through
 * /api/ui/stream.
 *
 * @param {{ method?: string, body?: unknown, requestId: string, signal?: AbortSignal }} [options]
 * @returns {Promise<{request_id: string, event_types: string[]}>}
 */
export async function requestAcknowledged(url, { method = 'GET', body, requestId, errorLabel, signal } = {}) {
  if (!isOperationRequestId(requestId)) {
    throw new Error('requestAcknowledged requires a UUID requestId');
  }
  const headers = {
    Accept: 'application/json',
    'X-Request-ID': requestId,
  };
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
  }

  const response = await fetch(url, {
    method,
    headers,
    credentials: 'include',
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    ...(signal ? { signal } : {}),
  });

  // Only 202 confirms that a finite browser request was dispatched.
  if (response.status !== 202) {
    const text = await response.text().catch(() => '');
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
    throw new Error(`${errorLabel || 'Request failed'} ${detail}`);
  }

  let acknowledgement;
  try {
    acknowledgement = JSON.parse(await response.text());
  } catch {
    throw new Error(`${errorLabel || 'Request failed'} invalid acknowledgement body`);
  }

  if (
    !acknowledgement
    || typeof acknowledgement !== 'object'
    || Array.isArray(acknowledgement)
    || Object.keys(acknowledgement).length !== 2
    || !Object.prototype.hasOwnProperty.call(acknowledgement, 'request_id')
    || !Object.prototype.hasOwnProperty.call(acknowledgement, 'event_types')
  ) {
    throw new Error(`${errorLabel || 'Request failed'} invalid acknowledgement body`);
  }
  if (acknowledgement.request_id !== requestId) {
    throw new Error(`${errorLabel || 'Request failed'} request_id mismatch`);
  }
  if (
    !Array.isArray(acknowledgement.event_types)
    || acknowledgement.event_types.length === 0
    || !acknowledgement.event_types.every((eventType) => typeof eventType === 'string' && eventType)
  ) {
    throw new Error(`${errorLabel || 'Request failed'} invalid event_types`);
  }

  return acknowledgement;
}
