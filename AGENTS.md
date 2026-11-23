# Repository Guidance

## Scope

This repository is an unofficial Windows client for Things Cloud. It is not
affiliated with or supported by Cultured Code.

## Source Map

- `things_cloud.py`: Cloud HTTP client, wire constants, state reconstruction.
- `sync_engine.py`: durable cache/outbox, validation, conflict handling and
  all safe task/project writes.
- `app.py`: localhost JSON API and static resources.
- `index.html`: Vue/Tailwind desktop UI.
- `main_window.py`: pywebview desktop shell.
- `tests/`: mock-Cloud and local API tests.

## Required Checks

Run before committing:

```powershell
python -m py_compile app.py things_cloud.py sync_engine.py main_window.py
python -m pytest -q
git diff --check
```

For packaging changes, also build and launch the one-file EXE and verify that
`index.html`, Vue and Tailwind are served from the PyInstaller resource path.

## Safety Rules

- Never add activation, licensing-bypass, signing-secret or credential files.
- Never log account emails, passwords, tokens, history keys or raw auth headers.
- Do not test writes against a real Things Cloud account or iOS device unless
  the user explicitly supplies approval and a recoverable account backup.
- Keep Task7 creates canonical: all 34 fields, no partial create payloads.
- Keep every outbound UUID and payload validated before network submission.
- Never silently retry a stale ancestor commit. Pull and compare first.
- Keep write tests on the in-memory fake Cloud by default.
- Do not force-push. Do not commit `build/`, `dist/`, `.spec`, logs or user data.

## Product Claims

Mock-Cloud tests do not prove real Cloud or iPhone interoperability. Keep the
"unverified" limitation in README and release notes until an actual account
and an iOS/macOS round-trip test have been documented.
