# Reply latency, 2026-10-22

Recent live metrics contained a 25.167 second reply: 12.125 seconds before
context construction, 1.696 seconds in the model, and 10.72 seconds sending
two texts and a native sticker. Other older replies spent 11–13 seconds in
the sending path. These are measured samples, not a guaranteed baseline.

Changes:

- Plain text and sticker jobs skip message-list descendants in AT-SPI snapshots.
  Destination row, title, composer, dialogs and sticker controls remain present.
  Quotes, pictures, forwarded records, Moments and normal reading use full trees.
- A visible favorite is clicked at its verified native index, without resetting
  the favorites scroll position or reselecting the already open tab. Native
  order is still checked immediately before the click.
- Concurrent account observations share one request. An account check overlaps
  model generation and can be reused at send time for at most two seconds.
  Expired checks are refreshed; failed checks are never cached as successes.
- Background sticker refresh and warning scans yield to interactive replies.
  Their functionality remains enabled and resumes while the chat worker is idle.
- Deployment reduces single-message debounce to .3 seconds, burst debounce to
  .9 seconds, maximum debounce to 2 seconds, and part spacing to .5 seconds.
  Account quotas and minimum sending/cooldown limits remain unchanged.
- Reply metrics separately record inbox delay, queue wait, debounce, account
  check and interval wait. Native jobs record snapshot count and duration.

Server acknowledgement, stable quote/media identities, account/destination
checks, action ledgers and the no-retry rule for uncertain sends remain intact.
Context sizes, memory, affection, search, native stickers and Moments switches
are not reduced. No native WeChat restart is required.

Validation uses focused unit checks and read-only live UI measurements.
Only subsequent real incoming chat metrics can establish the new end-to-end
reply latency; an empty queue or faster snapshot alone is not proof of it.
