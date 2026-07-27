---
name: managing-preferences
description: View or update the user's profile (display name, timezone) by editing the /config/preferences.json file. Use when a user asks to set/change their name or timezone, or asks what their current profile settings are.
allowed-tools: read_file write_file edit_file structured_output
---

# Managing Preferences

The user's profile lives in a single virtual file: `/config/preferences.json`. It
is backed by the runtime state document (Postgres is the source of truth), so
editing it is the supported way to change these settings — there is no separate
"update profile" tool. Every successful write is validated and the Settings page
updates live.

## When to Use
- User asks to set or change their display name
- User asks to set or change their timezone
- User asks what their current profile / preferences are

## File Schema
Authoritative shape: `/schemas/preferences.schema.v1.json`.

```json
{
  "display_name": "Ada Lovelace",
  "timezone": "America/New_York"
}
```

Rules enforced on every write (a violating edit is rejected and nothing changes):
- Top level must be a JSON object with exactly these two fields — no others.
- `display_name` is a string (may be empty).
- `timezone` is required and must be a non-empty IANA timezone name (e.g.
  `America/New_York`, `Europe/London`, `UTC`) — anything `zoneinfo` doesn't
  recognize is rejected.

## How to Update
1. `read_file /config/preferences.json`
2. For a single-field tweak (just the name, or just the timezone), `edit_file`
   it directly — no need for `structured_output`.
3. For several fields at once, `read_file /schemas/preferences.schema.v1.json`
   and run `structured_output` with the current document as `input` and an
   instruction describing the new end state, then `write_file` the result back
   in. This matters more as more fields get added to this document — for
   today's two fields, direct edits are usually simpler.

## Confirmation Pattern
After a successful edit, reply with one short sentence confirming what changed
(e.g. "Updated your timezone to America/New_York."). If the user names a city or
region instead of an IANA zone (e.g. "Eastern time" or "London"), map it to the
correct IANA name yourself rather than asking them to supply one.

## Troubleshooting
- Edit rejected as invalid: re-read the file — the most common cause is an
  unrecognized timezone name or a stray extra field.
- Timezone name unsure: prefer well-known IANA zones (`America/New_York`,
  `Europe/London`, `Asia/Tokyo`, `UTC`) over abbreviations like `EST`/`PST`,
  which are not valid IANA names.
