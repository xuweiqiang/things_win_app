# Architecture and Sync Protocol

## Goals

The client must make Windows a peer in an existing Things Cloud history:
changes created elsewhere must become visible on Windows, and Windows writes
must become visible to other clients after a successful commit.

The write path is intentionally conservative. An invalid UUID or incomplete
Task7 object can pollute a user's Cloud history, and a stale retry can overwrite
another device's work. All task/project writes therefore pass through
`sync_engine.py`.

## Process Architecture

```text
pywebview desktop window
          |
          v
Vue UI on 127.0.0.1:55234
          |
          v
app.py JSON API
          |
          v
SafeThings sync engine
   |               |
   v               v
atomic JSON      ThingsClient
cache/outbox          |
                      v
               Things Cloud history
```

`main_window.py` starts the localhost server in a daemon thread and points a
pywebview window at it. The HTTP server binds only to `127.0.0.1`.

## Cloud State

The account verify response supplies the history key. The client fetches
history items and tracks `latest-server-index`.

History entries contain an entity type, action and properties:

- `Task6` / `Task7`: tasks, projects and headings.
- `Area3`: areas.
- `Tag4`: tags.
- Created (`t=0`) and modified (`t=1`) actions.

`_rebuild_state` replays entries in order into a materialized view. Relationships
are stored as Base58 UUID arrays. Documents reconstruct parent project/area
names and tag names after replay.

## Canonical Task7 Creates

Safe creation uses the complete 34-field Task7 object derived from the Go
community SDK. Validation happens before any POST:

- UUID must decode to exactly 16 bytes in the Things Base58 alphabet.
- Create payloads must contain exactly the expected field set.
- Task type, schedule, status and trash values must be valid.
- `sr` and `tir` must agree.
- Notes must be a complete plain-text document object with correct CRC32.
- Relationship arrays contain valid UUIDs.
- A task cannot belong to both a project and an area.

Projects, headings and repeated tasks are not created by the Windows UI.
Offline edits remain separate modified operations and never rewrite an already
queued canonical create into a partial object.

## Durable Offline Writes

Each history key has two JSON files under `%APPDATA%\ThingsWindows\`:

- A cache containing the last known remote items and head index.
- An ordered outbox containing pending operations, local timestamps and
  conflict metadata.

Writes update the local projection and atomically persist the outbox before
returning to the UI. A successful Cloud commit persists the new head and cache,
then removes the outbox entry. Failure leaves it queued.

Later edits may coalesce with an existing modified operation, but they do not
coalesce into a created operation. This preserves the canonical create and
keeps create/status history valid for other Things clients.

## Conflict Handling

`ThingsClient.commit_once` performs one commit with an explicit
`ancestor-index`. It never retries a 409 internally.

On conflict, the engine:

1. Refreshes remote history.
2. Compares fields changed locally with fields changed remotely.
3. Merges the operation when field sets are disjoint.
4. Otherwise stores a durable conflict and leaves the local operation pending.

The UI can retry the local change after the latest remote state has been seen,
or abandon the local change in favor of remote state. Conflicts survive a
process restart.

## Schedules and Status

Schedule values use the Things `st` model:

- `0`: Inbox, no schedule date.
- `1`: Anytime or Today with an `sr` date.
- `2`: Someday or a future/explicit date.

Schedule updates always write `st`, `sr` and `tir` as a validated group.
Completion and restoration update `ss` with `sp`; incompatible legacy Task6
status transitions use Task6 where required.

## Compatibility Identity

The HTTP client reports a stable Things iPhone 3.22.8 identity and schema used
by the community protocol. This preserves Cloud version negotiation for older
iOS clients and avoids presenting an unknown platform/version pair.

This is a compatibility mechanism, not affiliation or a guarantee. Different
Things versions, server-side protocol changes, Apple account challenges and
2FA behavior need real-account interoperability testing.

## Security Boundary

- The local API binds to loopback only.
- Static resources are limited to a flat filename under the resource directory.
- Account passwords, when remembered, are encrypted with Windows DPAPI.
- The optional PIN adds a local prompt; it is not Apple two-factor auth.
- Attachment names and task UUIDs are sanitized before local file access.
- No Cloud credentials, history key or auth headers are written to test logs.

## Verification Status

Automated tests use a fake Cloud that enforces commit ancestors and calls the
same outbound validator. They prove queue, merge and API behavior, not the real
service. A real account and iPhone round trip remain outstanding.
