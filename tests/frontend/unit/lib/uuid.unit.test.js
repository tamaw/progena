// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createUuid } from '@frontend/lib/uuid';

const UUID_V4_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function expectUuidV4(value) {
  expect(value).toMatch(UUID_V4_PATTERN);
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('createUuid', () => {
  it('returns the native randomUUID value when Web Crypto provides it', () => {
    const nativeUuid = '11111111-1111-4111-8111-111111111111';
    const randomUUID = vi.fn(() => nativeUuid);
    vi.stubGlobal('crypto', { randomUUID });

    expect(createUuid()).toBe(nativeUuid);
  });
  it('constructs a v4 UUID from Web Crypto bytes when randomUUID is unavailable', () => {
    const getRandomValues = vi.fn((bytes) => {
      bytes.set([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]);
      return bytes;
    });
    vi.stubGlobal('crypto', { getRandomValues });

    const uuid = createUuid();
    expect(uuid).toBe('00010203-0405-4607-8809-0a0b0c0d0e0f');
    expectUuidV4(uuid);
  });

  it('constructs a v4 UUID when Web Crypto is unavailable', () => {
    vi.stubGlobal('crypto', undefined);
    vi.spyOn(Math, 'random').mockReturnValue(0);

    const uuid = createUuid();
    expect(uuid).toBe('00000000-0000-4000-8000-000000000000');
    expectUuidV4(uuid);
  });
});
