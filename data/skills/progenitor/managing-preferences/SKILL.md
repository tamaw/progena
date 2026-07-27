---
name: managing-preferences
description: View or update the user's profile (display name, timezone) and UI settings (theme, nav-rail collapse, pinned panel) by editing the /config/preferences.json file. Use when a user asks to set/change their name, timezone, theme/dark mode, or nav/panel layout, or asks what their current preferences are.
allowed-tools: read_file write_file edit_file structured_output
---

# Managing Preferences

The user's profile and UI settings live in a single virtual file:
`/config/preferences.json`. It is backed by the runtime state document
(Postgres is the source of truth), so editing it is the supported way to
change these settings — there is no separate "update profile" tool. Every
successful write is validated and the Settings page updates live.

The UI fields (`theme`, `nav_collapsed`, `pinned_panel`) are normally set from
the app itself (theme toggle, nav-rail collapse button, panel pin button), not
usually something a user asks the agent to change — but they're valid,
schema-validated fields like the rest of this document if a user does ask
("switch me to dark mode", "collapse the nav by default").

## When to Use
- User asks to set or change their display name
- User asks to set or change their timezone
- User asks to change their theme (light/dark/system) or nav-rail/panel layout
- User asks what their current profile / preferences are

## File Schema
Authoritative shape: `/schemas/preferences.schema.v1.json`.

```json
{
  "display_name": "Ada Lovelace",
  "timezone": "America/New_York",
  "theme": "system",
  "nav_collapsed": false,
  "pinned_panel": null
}
```

Rules enforced on every write (a violating edit is rejected and nothing changes):
- Top level must be a JSON object with exactly these five fields — no others.
- `display_name` is a string (may be empty).
- `timezone` is required and must be a non-empty IANA timezone name (e.g.
  `America/New_York`, `Europe/London`, `UTC`) — anything `zoneinfo` doesn't
  recognize is rejected.
- `theme` must be one of `system`, `dark`, `light`.
- `nav_collapsed` is a boolean.
- `pinned_panel` is `null` or one of `quickChat`, `alerts`, `insights` — the
  panel currently docked open instead of floating as a modal. `null` means
  none pinned. (`search`, `settings`, and `account` are never pinnable, so
  they're not valid values here.)

## How to Update
1. `read_file /config/preferences.json`
2. For a single-field tweak, `edit_file` it directly — no need for
   `structured_output`.
3. For several fields at once, `read_file /schemas/preferences.schema.v1.json`
   and run `structured_output` with the current document as `input` and an
   instruction describing the new end state, then `write_file` the result back
   in.

## Confirmation Pattern
After a successful edit, reply with one short sentence confirming what changed
(e.g. "Updated your timezone to America/New_York." or "Switched you to dark
mode."). If the user names a city or region instead of an IANA zone (e.g.
"Eastern time" or "London"), map it to the correct IANA name yourself rather
than asking them to supply one.

## Troubleshooting
- Edit rejected as invalid: re-read the file — the most common causes are an
  unrecognized timezone name, a stray extra field, or a `theme`/`pinned_panel`
  value outside the enum above.
- Timezone name unsure: prefer well-known IANA zones (`America/New_York`,
  `Europe/London`, `Asia/Tokyo`, `UTC`) over abbreviations like `EST`/`PST`,
  which are not valid IANA names.
