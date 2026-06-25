import { describe, expect, it } from "vitest";
import { NARROW_MQ } from "./usePaneCollapse";

describe("NARROW_MQ", () => {
  it("exports the shared breakpoint constant", () => {
    expect(NARROW_MQ).toBe("(max-width: 980px)");
  });
});

describe("paneClassName formula", () => {
  function build(base, collapsed) {
    return (base + " sess-list--collapsible " + (collapsed ? "is-collapsed" : "")).trim();
  }

  it("includes is-collapsed when collapsed", () => {
    expect(build("tasks-list", true)).toBe("tasks-list sess-list--collapsible is-collapsed");
  });

  it("omits is-collapsed when expanded", () => {
    expect(build("tasks-list", false)).toBe("tasks-list sess-list--collapsible");
  });

  it("trims whitespace when base is empty", () => {
    expect(build("", true)).toBe("sess-list--collapsible is-collapsed");
    expect(build("", false)).toBe("sess-list--collapsible");
  });

  it("handles multi-class base", () => {
    expect(build("chat-sessions-pane sess-list", false))
      .toBe("chat-sessions-pane sess-list sess-list--collapsible");
  });
});
