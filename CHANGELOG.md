# Changelog

## 3.0.4 - 2026-11-23

- Added the Things-style Upcoming (`计划`) list with every future scheduled
  task ordered by date, including scheduled Anytime and Someday tasks.
- Restored complete uncursored Task creations from older caches, fixing the
  missing `串口工具` item in Today while continuing to hide sparse legacy
  modifications.
- Focus the title field reliably whenever the new-task dialog opens.
- `Esc` now closes the topmost dialog or attachment preview exactly like its
  close button.

## 3.0.3 - 2026-11-23

- Rebuilt task state by replaying Cloud history in order, including deletes
  and tombstones, so stale or incomplete cache records no longer appear as
  real tasks.
- Hidden orphaned modifications, recurring templates and tasks under inactive
  project/heading ancestors from smart task lists.
- Cleared deleted tags from the task projection and preserved valid offline
  outbox items while ignoring uncursored legacy cache records.
- Added a single-instance process mutex. Launching the EXE again restores the
  existing window instead of starting a second client.
- Closing the window now minimizes it; the app remains in the taskbar and a
  background thread continues synchronizing every 15 seconds without prompts.

## 3.0.2 - 2026-11-23

- Added `Ctrl+T` as a global shortcut for creating a new task scheduled today.
- The shortcut opens the create dialog, preselects Today and focuses the title.

## 3.0.1 - 2026-11-23

- Fixed Today to use an exact local scheduled date instead of retaining every
  historical `sr` value from 2020 onward.
- Aligned Anytime, Someday and Upcoming filters with the community SDK.
- Added a monthly calendar view with scheduled-date and deadline markers.
- Added selected-day task filtering, date-scoped task creation and arbitrary
  scheduled-date editing.

## 3.0.0 - 2026-11-23

- Added durable offline cache and write-ahead outbox.
- Added safe, canonical Task7 task and project creation.
- Added bidirectional refresh, field-level merge and persistent conflict UI.
- Added task/project editing, deadlines, tags, notes, schedules and movement.
- Added completion, restoration, cancellation, trash and restore.
- Added a native pywebview desktop shell and self-contained EXE build.
- Removed activation/licensing flow and machine-bound license requirements.
- Replaced external frontend CDNs with bundled Vue and Tailwind assets.
- Added mock-Cloud sync tests and localhost API tests.
