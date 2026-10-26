import { createUuid } from '../lib/uuid';
import { describeFailureDetail, isCancellation } from '../lib/failures';

export class RequestError extends Error {
  constructor(message, { status, requestId, code, detail, cause } = {}) {
    super(message, { cause });
    this.name = 'RequestError';
    this.status = status;
    this.requestId = requestId;
    this.code = code || (status ? `HTTP_${status}` : 'REQUEST_FAILED');
    this.detail = detail;
  }
}
const sessionListeners = new Set();

/**
 * Observe whether the reverse proxy in front of the app accepts our sign-in.
 * Listeners receive `true` after a 401 and `false` after the next accepted
 * request. The open event stream keeps delivering after the sign-in lapses, so
 * request outcomes are the only signal that it did.
 */
export function subscribeSessionExpiry(listener) {
  sessionListeners.add(listener);
  return () => {
    sessionListeners.delete(listener);
  };
}

export function reportSessionExpiry(expired) {
  for (const listener of [...sessionListeners]) listener(expired);
}

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
 * The caller provides a *requestId* (UUID); it is sent as the X-Operation-Id
 * header. The acknowledgement contains only its matching request ID and the
 * causal UI event types. Domain data and terminal state arrive through
 * /api/ui/stream.
 *
 * @param {{ method?: string, body?: unknown, requestId: string, signal?: AbortSignal }} [options]
 * @returns {Promise<{request_id: string, event_types: string[]}>}
 */
export async function requestAcknowledged(url, { method = 'GET', body, requestId, signal } = {}) {
  if (!isOperationRequestId(requestId)) {
    throw new Error('requestAcknowledged requires a UUID requestId');
  }
  const headers = {
    Accept: 'application/json',
    'X-Operation-Id': requestId,
  };
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
  }

  let response;
  try {
    response = await fetch(url, {
      method,
      headers,
      credentials: 'include',
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      ...(signal ? { signal } : {}),
    });
  } catch (cause) {
    if (isCancellation(cause)) throw cause;
    throw new RequestError('Check your connection and try again.', { requestId, code: 'NETWORK_ERROR', cause });
  }

  if (response.status === 401) reportSessionExpiry(true);
  else if (response.status === 202) reportSessionExpiry(false);

  // Only 202 confirms that a finite browser request was dispatched.
  if (response.status !== 202) {
    const text = await response.text().catch(() => '');
    let detail;
    try {
      const errorBody = JSON.parse(text);
      detail = errorBody?.detail || errorBody?.message;
    } catch {
      // Non-JSON server bodies are not suitable UI messages.
    }
    throw new RequestError(
      describeFailureDetail(detail) || `The server returned an unexpected response (${response.status}).`,
      { status: response.status, requestId, detail },
    );
  }

  let acknowledgement;
  try {
    acknowledgement = JSON.parse(await response.text());
  } catch {
    throw new RequestError('The server returned an invalid acknowledgement.', { requestId, code: 'INVALID_ACKNOWLEDGEMENT' });
  }

  if (
    !acknowledgement
    || typeof acknowledgement !== 'object'
    || Array.isArray(acknowledgement)
    || Object.keys(acknowledgement).length !== 2
    || !Object.prototype.hasOwnProperty.call(acknowledgement, 'request_id')
    || !Object.prototype.hasOwnProperty.call(acknowledgement, 'event_types')
  ) {
    throw new RequestError('The server returned an invalid acknowledgement.', { requestId, code: 'INVALID_ACKNOWLEDGEMENT' });
  }
  if (acknowledgement.request_id !== requestId) {
    throw new RequestError('The server returned a mismatched request identifier.', { requestId, code: 'REQUEST_ID_MISMATCH' });
  }
  if (
    !Array.isArray(acknowledgement.event_types)
    || acknowledgement.event_types.length === 0
    || !acknowledgement.event_types.every((eventType) => typeof eventType === 'string' && eventType)
  ) {
    throw new RequestError('The server returned invalid event types.', { requestId, code: 'INVALID_EVENT_TYPES' });
  }

  return acknowledgement;
}
