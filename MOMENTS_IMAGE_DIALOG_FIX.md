# Moments image picker repair — 2026-10-22

The failed production publishing jobs contained two selected images. Previous
verification exercised text publishing only and did not cover that branch.

The native Qt file dialog restored its position to x=649 with width=630 on a
1024-pixel-wide Xvfb display. Its Open button was entirely beyond the screen
(x=1188), so the pointer click could not activate it. The file dialog remained
open and the subsequent publish-editor wait expired.

The image branch now centers and activates the unique Open window, reads fresh
on-screen bounds, fills the named File name field directly, and waits for an
enabled Open control before clicking once. Existing draft, authorization,
final submission, and native receipt checks remain intact.

A second directly observed fault was filesystem access: both the job directory
and its image subdirectory were mode 0700, owned by the core service account.
The native desktop could not access even one supplied PNG, so Open remained
disabled despite the filename field containing the exact correct paths.
Only the authorized image inputs are now copied to the separate readable
`/data/moments-publish-assets` directory (0755 directory, 0644 image files).
The original private job directory and its task/authorization files keep their
existing permissions. Image copies are retained; this repair deletes no files.

The deployment replaces only the native module and matching repository files.
It retains backups and old failed action records, preserves existing settings,
and does not restart WeChat, the reader, or core. Existing failed posts are not
automatically replayed. Live publishing evidence is recorded separately.

## Live result

At 2026-10-22 22:16 Beijing time, a newly generated post using the two currently
selected images was published through the normal authenticated API and GUI job
path. Its state is `sent`, native receipt is `15038078093219279396`, and submission
plus native readback took 13.5 seconds. A subsequent native read found the own
post with `image_count=2`. The desktop user can read both staged PNGs.
Existing failed actions remain unchanged, no uncertain publish is pending, and
native WeChat retains PID 1697250. No phone-side friend-feed check was performed.

Evidence: `/opt/whalegirl-linux-assets/moments-image-publish-verification.json`.
