import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';

/**
 * Replace a detail URL only after its backing collection has hydrated and the
 * routed entity is absent. Until hydration, a deep link remains authoritative.
 */
function useMissingEntityRoute({ hydrated, entityId, entities, getEntityId, fallbackPath }) {
  const navigate = useNavigate();

  useEffect(() => {
    if (!hydrated || !entityId) {
      return;
    }
    if (!entities.some((entity) => getEntityId(entity) === entityId)) {
      navigate(fallbackPath, { replace: true });
    }
  }, [entities, entityId, fallbackPath, getEntityId, hydrated, navigate]);
}

export default useMissingEntityRoute;
