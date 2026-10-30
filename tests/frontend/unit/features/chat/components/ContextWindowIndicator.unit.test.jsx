// @vitest-environment jsdom
import React from 'react';
import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import ContextWindowIndicator from '@frontend/features/chat/components/ContextWindowIndicator';

function render(usage) {
  const container = document.createElement('div');
  container.innerHTML = renderToStaticMarkup(<ContextWindowIndicator usage={usage} />);
  return container;
}

describe('ContextWindowIndicator', () => {
  it.each([null, { usedTokens: 10 }, { usedTokens: 10, maxTokens: 0 }, { usedTokens: 10, maxTokens: '100' }, { maxTokens: 100 }])('hides unavailable context capacity or usage: %j', (usage) => {
    expect(render(usage).querySelector('[role="meter"]')).toBeNull();
  });

  it.each([[0, '0%'], [250, '25%'], [1000, '100%']])('fills to the used fraction for %i tokens', (usedTokens, fill) => {
    const meter = render({ usedTokens, maxTokens: 1000 }).querySelector('[role="meter"]');
    expect(meter.getAttribute('aria-valuenow')).toBe(String(usedTokens));
    expect(meter.getAttribute('aria-valuemax')).toBe('1000');
    expect(meter.firstElementChild.style.getPropertyValue('--context-fill')).toBe(fill);
  });

  it('caps the pie at capacity while retaining the actual footprint in its description', () => {
    const meter = render({ usedTokens: 1200, maxTokens: 1000 }).querySelector('[role="meter"]');
    expect(meter.getAttribute('aria-valuenow')).toBe('1000');
    expect(meter.getAttribute('aria-valuetext')).toContain('1,200 / 1,000');
    expect(meter.firstElementChild.style.getPropertyValue('--context-fill')).toBe('100%');
  });

  it('reports the observed input reduction without erasing retained context', () => {
    const meter = render({
      usedTokens: 12500, maxTokens: 100000,
      compaction: { beforeInputTokens: 80000, afterInputTokens: 12000, freedInputTokens: 68000 },
    }).querySelector('[role="meter"]');
    expect(meter.getAttribute('aria-valuenow')).toBe('12500');
    expect(meter.getAttribute('aria-valuetext')).toContain('input decreased by 68,000 tokens');
    expect(meter.getAttribute('aria-valuetext')).toContain('not exact tokenizer savings');
    expect(meter.getAttribute('aria-valuetext')).toContain('Lifetime usage is unchanged');
  });
});
