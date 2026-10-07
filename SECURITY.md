# Security policy

## Supported versions

Only the latest version on `main` receives security fixes.

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private vulnerability reporting: **Security > Report a vulnerability**
on this repository. Include the steps to reproduce and the commit you tested. You will get an answer within 7 days;
a fix and an advisory follow as soon as possible and within 90 days, crediting you unless you prefer otherwise.

## Threat model

Tallyhound is an audit console for one team, not a multi-tenant service.

It is built to withstand:

- **Hostile uploaded files.** Text from files and models is escaped before display; spreadsheet output keeps
  formula-like cells as text; zips are size-limited (also once unpacked), lines are length-limited, and nothing from a
  zip is ever used as a file path. Invoice PDFs are parsed in a separate process with a time limit and, on Linux and
  macOS, a memory limit (Windows has no per-process memory limit here; the time limit still applies).
  Regular expressions applied to uploads are written so they cannot backtrack catastrophically; a test sweeps them.
- **Tampering with the audit trail.** Entries are hash-chained and the whole trail is sealed with a key held by the
  server, so edited, removed, added, reordered or truncated entries - and decisions that disagree with the trail - are
  reported. (Someone with the key and write access to the server can of course rewrite everything.)
- **Password guessing.** PBKDF2 hashes, constant-time comparison, a per-name lockout after five wrong passwords.
- **Session fixation.** With sign-in on, saved work is a server-side team workspace loaded only after sign-in; the page
  address is ignored, and signing out clears the browser tab.
- **Server-side request forgery on public copies.** The model address accepts only http(s), never link-local or
  cloud-metadata addresses, follows no redirects, and on a public copy (`TALLYHOUND_PUBLIC=1`, or Streamlit Community
  Cloud) only `localhost`.

It does not defend against:

- **Anyone who has a demo session link** (sign-in off): the `?s=...` address is the key to that work.
- **Anyone with access to the server** or its state folder.
- **The model server you point it at.** Run models locally; their output is treated as untrusted text.
- **Determined denial of service** on an open public instance (many large uploads at once). Put a rate limit in front.
- **Locking out a known user name** by entering wrong passwords for it (the lockout is per name, by design).

By default sign-in is off and every visitor can do everything. That suits the bundled fictional demo data only, never
real company data.

## Hardening a deployment

1. **Turn sign-in on**: `python scripts/add_user.py <name> <role>`. Give preparers and reviewers separate accounts.
2. **Set `TALLYHOUND_TRAIL_KEY`** to a long random secret kept outside the state folder, and back it up: if it is lost,
   saved trails show as broken.
3. **Set `TALLYHOUND_PUBLIC=1`** on any instance reachable from the internet.
4. **Put a reverse proxy with HTTPS in front**, with limits on request size and rate. Add authentication at the proxy
   if you can.
5. **Keep the state folder private**: set `TALLYHOUND_STATE_DIR` to a folder owned by the service user. Tallyhound
   creates it with mode 0700 and `users.json`, `trail.key` and `tallyhound.db` with 0600; keep them that way.
6. **Back up** the state folder and the trail key, encrypted.
7. **Stay current**: `pip install -U -r requirements.txt` regularly (Streamlit and pypdf especially).
8. **Treat links as secrets**: do not paste demo session links into tickets or chat.
