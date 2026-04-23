import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { StreamEvents } from './Message';

describe('StreamEvents', () => {
  it('strips inline event handlers from bodyHtml before rendering', () => {
    render(
      <StreamEvents
        events={[{ id: 'evt-1', eventType: 'custom' }]}
        describeEvent={() => ({
          badge: 'Event',
          title: 'Unsafe HTML',
          bodyHtml: '<img src="x" onerror="alert(1)" /><span>safe</span>',
        })}
      />
    );

    const body = document.querySelector('.message-stream-event-body');
    const image = body?.querySelector('img');

    expect(body?.innerHTML).not.toContain('onerror=');
    expect(image?.getAttribute('onerror')).toBeNull();
    expect(body?.textContent).toContain('safe');
  });
});
