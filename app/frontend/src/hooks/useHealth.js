import { useContext } from 'react';
import { HealthContext } from '../context/HealthContext';

export function useHealth() {
  const context = useContext(HealthContext);
  if (!context) {
    throw new Error('useHealth must be used within a HealthProvider');
  }
  return context;
}
