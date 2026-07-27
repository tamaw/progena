/**
 * A short, synthesized two-tone chime for "important work completed" alerts
 * (see Settings > Notifications > "Notification sounds"). No audio asset
 * exists in this repo, so this is generated with the Web Audio API rather
 * than adding a static file + a Vite public/ dir for one short sound.
 */

let audioContext = null;

function getAudioContext() {
  if (typeof window === 'undefined') {
    return null;
  }
  const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextCtor) {
    return null;
  }
  if (!audioContext) {
    audioContext = new AudioContextCtor();
  }
  return audioContext;
}

// Rising-fifth chime (A5 -> E6), similar to common "task done" cues.
const TONES = [
  { freq: 880, start: 0, duration: 0.12 },
  { freq: 1318.51, start: 0.1, duration: 0.18 },
];

export function playNotificationSound() {
  try {
    const ctx = getAudioContext();
    if (!ctx) {
      return;
    }
    if (ctx.state === 'suspended') {
      // Browsers suspend AudioContext until a user gesture occurs anywhere on
      // the page; by the time an alert can arrive the user has necessarily
      // interacted with the app already, but resume() is cheap insurance.
      ctx.resume().catch(() => {});
    }
    const now = ctx.currentTime;
    for (const { freq, start, duration } of TONES) {
      const oscillator = ctx.createOscillator();
      const gain = ctx.createGain();
      oscillator.type = 'sine';
      oscillator.frequency.value = freq;
      gain.gain.setValueAtTime(0, now + start);
      gain.gain.linearRampToValueAtTime(0.15, now + start + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + start + duration);
      oscillator.connect(gain);
      gain.connect(ctx.destination);
      oscillator.start(now + start);
      oscillator.stop(now + start + duration + 0.02);
    }
  } catch {
    // Never let a sound failure (blocked autoplay, unsupported browser, etc.)
    // break the toast it's paired with.
  }
}
