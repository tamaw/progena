import { useState } from 'react';

/**
 * Open while `running`, collapsed once finished -- until the user toggles it,
 * after which their choice sticks regardless of status.
 */
export default function useRunningDisclosure(running) {
  const [userOpen, setUserOpen] = useState(null);
  const open = userOpen ?? running;
  return { open, toggle: () => setUserOpen(!open) };
}
