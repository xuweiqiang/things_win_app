# Changelog

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
