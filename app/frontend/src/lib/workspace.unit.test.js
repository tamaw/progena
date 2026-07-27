import { describe, it, expect, beforeEach } from 'vitest';
import {
  parseWorkspacePath,
  currentPageHost,
  inferScheme,
  resolveWorkspaceOrigin,
  buildWorkspacePath,
  normalizeWorkspaceHost,
  getWorkspaceRegistry,
  ensureWorkspaceRegistered,
  addWorkspaceHost,
  removeWorkspaceHost,
} from './workspace';

// Node test environment has no `window` -- stub just enough for the one
// function that touches it (currentPageHost).
beforeEach(() => {
  globalThis.window = { location: { host: 'app.internal:5173', href: '' } };
});

describe('parseWorkspacePath', () => {
  it('extracts host and rest from a workspace path', () => {
    expect(parseWorkspacePath('/w/team-a.example.com/chat/thread-1')).toEqual({
      host: 'team-a.example.com',
      rest: '/chat/thread-1',
    });
  });

  it('preserves a port in the host segment', () => {
    expect(parseWorkspacePath('/w/localhost:8000/chat')).toEqual({
      host: 'localhost:8000',
      rest: '/chat',
    });
  });

  it('defaults rest to "/" when the path is just the host', () => {
    expect(parseWorkspacePath('/w/team-a.example.com')).toEqual({
      host: 'team-a.example.com',
      rest: '/',
    });
  });

  it('returns null for a non-workspace path', () => {
    expect(parseWorkspacePath('/chat/thread-1')).toBeNull();
  });

  it('returns null for the bare root', () => {
    expect(parseWorkspacePath('/')).toBeNull();
  });
});

describe('currentPageHost', () => {
  it('reads the host the frontend was served from', () => {
    expect(currentPageHost()).toBe('app.internal:5173');
  });
});

describe('inferScheme', () => {
  it('infers http for localhost', () => {
    expect(inferScheme('localhost:8000')).toBe('http');
  });

  it('infers http for loopback', () => {
    expect(inferScheme('127.0.0.1:8000')).toBe('http');
  });

  it('infers http for a 192.168.x.x LAN address', () => {
    expect(inferScheme('192.168.0.2')).toBe('http');
  });

  it('infers http for a 10.x.x.x private address', () => {
    expect(inferScheme('10.1.2.3')).toBe('http');
  });

  it('infers http for a 172.16-31.x.x private address', () => {
    expect(inferScheme('172.16.0.5')).toBe('http');
    expect(inferScheme('172.31.255.255')).toBe('http');
  });

  it('does not treat 172.x outside 16-31 as private', () => {
    expect(inferScheme('172.32.0.5')).toBe('https');
    expect(inferScheme('172.15.0.5')).toBe('https');
  });

  it('infers https for a real DNS name', () => {
    expect(inferScheme('team-a.example.com')).toBe('https');
  });

  it('infers https for a public IPv4 address', () => {
    expect(inferScheme('8.8.8.8')).toBe('https');
  });

  it('infers http for a bare unqualified hostname', () => {
    expect(inferScheme('cronix')).toBe('http');
    expect(inferScheme('cronix:8000')).toBe('http');
  });
});

describe('resolveWorkspaceOrigin', () => {
  it('combines the inferred scheme with the host', () => {
    expect(resolveWorkspaceOrigin('team-a.example.com')).toBe('https://team-a.example.com');
    expect(resolveWorkspaceOrigin('localhost:8000')).toBe('http://localhost:8000');
  });
});

describe('buildWorkspacePath', () => {
  it('joins host and rest under /w/', () => {
    expect(buildWorkspacePath('team-a.example.com', '/chat')).toBe('/w/team-a.example.com/chat');
  });

  it('adds a leading slash to rest when missing', () => {
    expect(buildWorkspacePath('team-a.example.com', 'chat')).toBe('/w/team-a.example.com/chat');
  });

  it('defaults rest to the app root', () => {
    expect(buildWorkspacePath('team-a.example.com')).toBe('/w/team-a.example.com/');
  });
});

describe('normalizeWorkspaceHost', () => {
  it('accepts a bare hostname', () => {
    expect(normalizeWorkspaceHost('team-a.example.com')).toBe('team-a.example.com');
  });

  it('accepts a hostname with a port', () => {
    expect(normalizeWorkspaceHost('localhost:8001')).toBe('localhost:8001');
  });

  it('lowercases mixed-case input', () => {
    expect(normalizeWorkspaceHost('Team-A.Example.COM')).toBe('team-a.example.com');
  });

  it('strips a pasted https:// scheme', () => {
    expect(normalizeWorkspaceHost('https://team-a.example.com')).toBe('team-a.example.com');
  });

  it('strips a pasted http:// scheme', () => {
    expect(normalizeWorkspaceHost('http://localhost:8001')).toBe('localhost:8001');
  });

  it('strips a trailing path', () => {
    expect(normalizeWorkspaceHost('team-a.example.com/chat/thread-1')).toBe('team-a.example.com');
  });

  it('strips a full pasted URL down to host:port', () => {
    expect(normalizeWorkspaceHost('https://localhost:8001/w/x/chat?foo=bar#frag')).toBe('localhost:8001');
  });

  it('trims surrounding whitespace', () => {
    expect(normalizeWorkspaceHost('  team-a.example.com  ')).toBe('team-a.example.com');
  });

  it('rejects empty input', () => {
    expect(normalizeWorkspaceHost('')).toBeNull();
    expect(normalizeWorkspaceHost('   ')).toBeNull();
  });

  it('rejects internal whitespace', () => {
    expect(normalizeWorkspaceHost('team a.example.com')).toBeNull();
  });

  it('rejects non-string input', () => {
    expect(normalizeWorkspaceHost(null)).toBeNull();
    expect(normalizeWorkspaceHost(undefined)).toBeNull();
  });

  it('rejects a non-numeric port', () => {
    expect(normalizeWorkspaceHost('localhost:abc')).toBeNull();
  });
});

describe('workspace registry', () => {
  it('starts empty', () => {
    expect(getWorkspaceRegistry()).toEqual([]);
  });

  it('ensureWorkspaceRegistered adds a new host', () => {
    ensureWorkspaceRegistered('team-a.example.com');
    expect(getWorkspaceRegistry()).toEqual(['team-a.example.com']);
  });

  it('ensureWorkspaceRegistered is idempotent', () => {
    ensureWorkspaceRegistered('team-a.example.com');
    ensureWorkspaceRegistered('team-a.example.com');
    expect(getWorkspaceRegistry()).toEqual(['team-a.example.com']);
  });

  it('addWorkspaceHost normalizes and registers valid input', () => {
    expect(addWorkspaceHost('HTTPS://Team-B.Example.com/chat')).toBe(true);
    expect(getWorkspaceRegistry()).toEqual(['team-b.example.com']);
  });

  it('addWorkspaceHost rejects invalid input and registers nothing', () => {
    expect(addWorkspaceHost('not a host!!')).toBe(false);
    expect(getWorkspaceRegistry()).toEqual([]);
  });

  it('removeWorkspaceHost drops only the matching host', () => {
    ensureWorkspaceRegistered('team-a.example.com');
    ensureWorkspaceRegistered('team-b.example.com');
    removeWorkspaceHost('team-a.example.com');
    expect(getWorkspaceRegistry()).toEqual(['team-b.example.com']);
  });

  it('survives a corrupted registry value', () => {
    localStorage.setItem('progenitor_workspaces', 'not json');
    expect(getWorkspaceRegistry()).toEqual([]);
  });
});
