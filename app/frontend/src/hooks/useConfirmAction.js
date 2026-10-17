import { useCallback, useEffect, useRef, useState } from 'react';
import useUiAction from './useUiAction';

export default function useConfirmAction(scopeKey) {
  const { run, clearError, busy, error } = useUiAction(scopeKey);
  const [target, setTarget] = useState(null);
  const confirming = useRef(null);
  useEffect(() => { setTarget(null); confirming.current = null; }, [scopeKey]);
  const request = useCallback((operation, task) => {
    if (confirming.current) return;
    clearError();
    setTarget({ scopeKey, operation, task });
  }, [clearError, scopeKey]);
  const close = useCallback(() => {
    if (!confirming.current) setTarget(null);
  }, []);
  const confirm = useCallback(async () => {
    if (!target || target.scopeKey !== scopeKey || confirming.current) return;
    const token = Symbol();
    confirming.current = token;
    try {
      const result = await run(target.operation, target.task);
      if (result.ok) setTarget((current) => current === target ? null : current);
      return result;
    } finally {
      if (confirming.current === token) confirming.current = null;
    }
  }, [run, scopeKey, target]);
  return {
    open: target?.scopeKey === scopeKey && target !== null,
    busy, error,
    request, close, confirm,
  };
}
