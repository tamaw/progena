const UUID_V4_VERSION = 0x40;
const UUID_RFC4122_VARIANT = 0x80;

function formatUuid(bytes) {
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function fallbackBytes() {
  const bytes = new Uint8Array(16);
  const webCrypto = globalThis.crypto;

  if (typeof webCrypto?.getRandomValues === 'function') {
    webCrypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }

  bytes[6] = (bytes[6] & 0x0f) | UUID_V4_VERSION;
  bytes[8] = (bytes[8] & 0x3f) | UUID_RFC4122_VARIANT;
  return bytes;
}

/**
 * Create a UUID suitable for correlating finite browser requests.
 *
 * Some supported WebViews expose `crypto` but not `crypto.randomUUID()`. Use
 * the native implementation when present, otherwise construct an RFC 4122 v4
 * UUID from Web Crypto random bytes. The final Math.random fallback preserves
 * request correlation in legacy runtimes without Web Crypto; it is not for
 * security-sensitive identifiers.
 */
export function createUuid() {
  const webCrypto = globalThis.crypto;
  return typeof webCrypto?.randomUUID === 'function'
    ? webCrypto.randomUUID()
    : formatUuid(fallbackBytes());
}
