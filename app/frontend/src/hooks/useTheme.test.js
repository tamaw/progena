import { describe, expect, it } from "vitest";
import { resolveTheme } from "./useTheme";

describe("resolveTheme", () => {
  it("returns dark for dark theme regardless of system preference", () => {
    expect(resolveTheme("dark", false)).toBe("dark");
    expect(resolveTheme("dark", true)).toBe("dark");
  });

  it("returns light for light theme regardless of system preference", () => {
    expect(resolveTheme("light", false)).toBe("light");
    expect(resolveTheme("light", true)).toBe("light");
  });

  it("returns dark for system when prefers-dark is true", () => {
    expect(resolveTheme("system", true)).toBe("dark");
  });

  it("returns light for system when prefers-dark is false", () => {
    expect(resolveTheme("system", false)).toBe("light");
  });

  it("returns light for system when prefersDark is undefined (not passed)", () => {
    // Ensures callers always pass prefersDark explicitly — undefined coerces to
    // false so the result is light (same as system-prefers-light), but callers
    // should always pass window.matchMedia(...).matches.
    expect(resolveTheme("system", undefined)).toBe("light");
  });
});
