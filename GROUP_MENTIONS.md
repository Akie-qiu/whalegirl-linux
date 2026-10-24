# Native group mentions

- `group_mentions_enabled` is a global switch, default on, exposed in the
  Conversations settings beside native quoted replies. Other settings saves
  preserve its value. It applies only to group messages.
- The read-only native reader exposes the authorized current group's roster,
  never a cross-group/global contact search. The gateway caches it for one minute.
- The model selects local `m1`-style tokens within its existing reply call;
  exact known inline `@name` outputs are normalized too. Unknown targets are
  ignored. Each text action can mention at most three members. No @all support.
- Native insertion types @ and selects the actual member from the pinned
  client's popup. Duplicate names with no unique search alias are not guessed.
  If the picker fails, only the text body is sent and the fallback is recorded.
- The switch is checked again natively before submission. Existing quoting,
  stickers, limits, and single-submit/no-resend behavior remain in place.
- Native outgoing `atuserlist` metadata is exposed as `mentionedUserIds` and
  read back to verify the reminder, separately from plain @name text.
- No additional model request is needed; non-mention messages keep their normal
  UI send path. Old records and failed jobs are retained.
