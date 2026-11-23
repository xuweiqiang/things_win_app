# Things Windows Unofficial Client

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Español](README.es.md)

An independent Windows desktop client that reads and writes Things Cloud
through the public community protocol, keeping tasks synchronized with Things
on macOS, iPhone, and iPad.

> **Unofficial project:** This project is not affiliated with, supported by,
> or endorsed by Cultured Code. Things is a trademark of Cultured Code.
> Using the private Cloud protocol involves compatibility and account risks.
> Back up important data first.

## Features

- Native desktop window powered by pywebview and Microsoft Edge WebView2.
- Pull remote changes from Things Cloud and submit Windows edits back to the
  same history.
- Create and edit tasks and projects, including titles, notes, deadlines,
  tags, and project/area membership.
- Inbox, Today, Upcoming, Calendar, Anytime, Someday, Logbook, Trash, and
  project views.
- Monthly calendar view with scheduled-date and deadline markers. Select a day
  to see its tasks.
- Create tasks on a selected calendar date, or change any scheduled date from
  the detail panel.
- Press `Ctrl+T` to open the new-task dialog from anywhere and place the task
  in Today.
- Complete, restore, cancel, trash, restore from trash, and reorder tasks.
- Local disk cache, durable offline write queue, automatic retry, and a
  conflict-resolution interface.
- Windows DPAPI-protected credentials with optional local PIN verification.
- Store attachments locally on Windows; only attachment marker text is
  synchronized in notes.

## Not Yet Verified

Real Things Cloud account writes and iPhone round-trip synchronization were
not verified for this release. Current verification covers the local HTTP API,
durable queue, conflict policy, and bidirectional commits against a simulated
Things Cloud.

The following are compatibility designs, not proven interoperability
guarantees:

- The client generates complete Task7 creation payloads according to the
  community Go SDK rules.
- The client uses the community-SDK-tested `ThingsMac/32209501` compatibility
  identity and schema.
- After a successful commit, macOS/iOS clients reading the same history should
  see Windows changes.

Scenarios not yet verified with a real account include existing-account login,
Apple account security prompts, two-factor authentication, different
Things/iOS versions, recurring tasks, and Cloud rate limits. Use a test account
or a restorable backup for initial validation.

## Requirements

- Windows 10/11 x64.
- Python 3.10 or newer when running from source.
- Microsoft Edge WebView2 Runtime (normally included with Windows 10/11).
- An active Things Cloud account.

Install and start:

```powershell
python -m pip install -r requirements-dev.txt
python main_window.py
```

You can also double-click `启动.bat`. If a v3 executable exists in `dist`, it
starts the EXE first; otherwise it runs `main_window.py`.

To debug only the backend in a browser:

```powershell
python app.py
```

The backend listens only on `127.0.0.1:55234` by default.

## Build the EXE

Double-click `build_onefile.bat`, or run:

```powershell
python -m PyInstaller --noconfirm --clean --onefile --windowed `
  --name ThingsWindows_v3.0.4 `
  --add-data "index.html;." `
  --add-data "vue.global.prod.js;." `
  --add-data "tailwindcss.js;." `
  --collect-all webview `
  main_window.py
```

The executable is written to `dist\ThingsWindows_v3.0.4.exe`. `build/`,
`dist/`, logs, and runtime data are not committed to Git.

## Tests

```powershell
python -m py_compile app.py things_cloud.py sync_engine.py main_window.py
python -m pytest -q
```

Tests use an in-memory simulated Things Cloud and never access a real account.
Coverage includes creation, editing, project/area moves, schedules, deadlines,
tags, completion/cancellation, trash, offline-queue restart, remote-change
merging, same-field conflicts, conflict retry/abandon, and API static assets.

## Synchronization Behavior and Limits

- Writes enter a disk-backed outbox and leave it only after Cloud succeeds.
- Commits use `ancestor-index`; on HTTP 409 the client pulls remote changes
  before deciding whether a conflict exists.
- Concurrent local and remote edits to the same field are never silently
  overwritten. The UI offers retry-local or discard-local actions.
- Concurrent edits to different fields are merged where possible.
- Recurring-task modifications are currently rejected to avoid damaging the
  history.
- Permanent deletion is not implemented; only recoverable Trash is supported.
- Attachment contents are not uploaded to Things Cloud. Only attachment
  markers in notes are synchronized.
- Notes are stored as Things plain-text objects with a CRC32 checksum.

See the [Chinese architecture and protocol notes](docs/ARCHITECTURE.md) for
implementation details.

## Data Locations

Application-level data is stored under `%APPDATA%\ThingsWindows\`. Cache data
for each Cloud history is stored under
`%APPDATA%\ThingsWindows\accounts\<history-hash>\`:

- `history_items.json`: local Cloud state cache.
- `known_head.json`: last confirmed remote head.
- `outbox.json`: write operations waiting for submission.
- `conflicts.json`: persisted conflict records.
- `sync_state.json`: latest synchronization state.
- `credentials.dat`: DPAPI-encrypted credentials in the application directory.
- `pins.json`: pinned UI items in the application directory.
- `attachments\`: local attachments in the application directory.

Logs are written to `things_debug.log` beside the EXE or source checkout. This
file is ignored by Git.

## References and License

Synchronization protocol references:

- [arthursoares/things-cloud-sdk](https://github.com/arthursoares/things-cloud-sdk)
- [evanpurkhiser/things3-cloud](https://github.com/evanpurkhiser/things3-cloud)

This project preserves the community source's MIT license and copyright
notices; see [LICENSE](LICENSE). The bundled Vue and Tailwind browser builds
also retain their respective MIT license headers.
