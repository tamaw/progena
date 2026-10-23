export function isCancellation(error) {
  return error?.name === 'AbortError' || error?.code === 'ABORT_ERR';
}

export function describeFailureDetail(detail) {
  if (typeof detail === 'string') return detail.trim();
  if (Array.isArray(detail)) {
    return detail.map((item) => {
      if (typeof item === 'string') return item;
      const field = Array.isArray(item?.loc) ? item.loc.filter((part) => part !== 'body').join('.') : '';
      return item?.msg ? `${field ? `${field}: ` : ''}${item.msg}` : '';
    }).filter(Boolean).join('\n');
  }
  return typeof detail?.message === 'string' ? detail.message.trim() : '';
}

export function failureMessage(error, operation = 'Complete request') {
  if (error?.title && typeof error.message === 'string') return error;
  const label = operation.trim() || 'Complete request';
  const status = error?.status || Number(/^HTTP_(\d+)$/.exec(error?.code || '')?.[1]);
  let message = describeFailureDetail(error?.detail)
    || (typeof error === 'string' ? error.trim() : error?.message?.trim?.()) || 'Please try again.';
  if (status === 401) message = 'Sign in again, then retry.';
  else if (status === 403) message = 'You don’t have permission to perform this action.';
  else if (error?.code === 'NETWORK_ERROR') message = 'Check your connection and try again.';
  return { title: `Couldn’t ${label[0].toLowerCase()}${label.slice(1)}`, message };
}

export function reportFailure(show, operation, error) {
  if (isCancellation(error)) return;
  const failure = failureMessage(error, operation);
  return show('error', failure.title, failure.message);
}
