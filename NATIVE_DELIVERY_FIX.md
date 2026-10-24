# Native delivery repairs — 2026-10-22

- Favorite selection formerly handled the first and last virtual pages only. Middle favorites now use a top anchor and align stable accessible object identities on overlapping pages. Native wheel acceleration makes pixel-only offsets unreliable. Native order and destination checks remain in place.
- Sticker popups close in `finally`, including failures before submission. Moments dismisses leftover sticker overlays before clicking the sidebar. Moments snapshots omit chat message bodies and name the stage when a control times out.
- Process creation retries only `OSError(EAGAIN)` after short delays. An exited command, timed-out command, or unconfirmed send is never retried by this helper.
- Moments exposes contact remarks/nicknames for cached timeline authors and selected friends. The console refreshes labels after chats or Moments data arrive and preserves unsaved selections. IDs remain the saved values.
- Deployment preserves settings, database backups, old releases, audit records, and the running native WeChat session. Only core and the read-only reader restart.
- Native verification intercepts the final sticker click and cancels the typed Moments draft. These checks do not establish phone-side delivery of a newly published post. Old failed posts/messages are not automatically replayed.
- Verification cleanup explicitly clears its own synthetic text before Cancel: the pinned client retains a nonempty cancelled draft. The live synthetic draft was identified by exact AT-SPI text, cleared, and the editor/window were confirmed closed. Other drafts are preserved.

## Live verification

- Release: `/opt/whalegirl-linux/releases/native-delivery-20261022-211349`.
- Native favorite indexes 0, 25, 30, 55 reached their final click with that click intercepted; no test messages were submitted.
- Moments opened from a leftover sticker popup and reached the text editor and final Post control without publishing.
- The mobile console at 390px width showed 19 friend choices, resolved the selected friend's nickname, and reported no JavaScript errors.
- Native WeChat stayed logged in with the same PID. Existing settings and failed-action audit records were preserved.
