# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A fork of [schmij97/lms-ytmusic](https://github.com/schmij97/lms-ytmusic), a
Lyrion Music Server (LMS, formerly Logitech Media Server) plugin written in
Perl + Python that browses, searches, and streams YouTube Music to Squeezebox
players via YouTube Music's unofficial InnerTube web API. There is no build
step for the plugin itself — Perl runs as-is inside LMS's process; the Python
side (`ytmproxy.py`) runs as a local sidecar process.

This fork tracks upstream closely (merge upstream in, send fixes upstream as
PRs) — see `FORK.md` for the merge/versioning workflow, which is the first
thing to read before changing version numbers or `repo.xml`.

## Commands

```
tools/test-compile.sh   # Perl syntax (Slim::* stubbed via StubInc.pm), Python
                         # byte-compile, install.xml/repo.xml well-formedness
```

That's the only automated check in this repo — no per-module unit tests like
`lms-drlive` has. It verifies syntax/load-order and XML validity, not runtime
behavior against a real LMS instance or the live YouTube Music API. To
exercise actual behavior, load the plugin into a real LMS instance and watch
`server.log` with the `plugin.youtubemusic` category at Debug, or hit the
proxy directly: `curl http://127.0.0.1:9876/ping`.

### Releasing

```
tools/release.sh 1.4.7.1            # run tests, bump version, rebuild dist/*.zip, rewrite repo.xml
tools/release.sh 1.4.7.1 --publish  # ...then git commit, tag v1.4.7.1, push, and create the GitHub release
```

Fork versions are upstream's version plus a fourth number (`1.4.7.1`,
`1.4.7.2`, then `1.4.8.1` after merging upstream `1.4.8`) so they always sort
above the upstream release they're based on. `install.xml`'s `<version>` and
`repo.xml`'s `<version>`/`<url>`/`<sha>` all move together — same pattern as
`lms-drlive` — and the script fails loudly if any didn't take. Edit
`repo.xml`'s `<changes>` by hand before releasing; the build is reproducible
(file mtimes pinned to the last code-touching commit, sorted order) so a dry
run and `--publish` produce the same zip/SHA-1.

### Merging upstream

```
git fetch upstream
git merge upstream/main
```

`repo.xml` is `merge=ours` in `.gitattributes` so upstream's URL/checksum/
version never overwrite this fork's — but the `merge.ours.driver` git config
has to be enabled once per clone (see `FORK.md`). `install.xml`'s `<version>`
line usually conflicts; take either side, `release.sh` rewrites it anyway.

## Architecture

```
Device → LMS (Plugin.pm / ProtocolHandler.pm / PlaylistProtocolHandler.pm)
            → ytmproxy.py (127.0.0.1:9876, InnerTube API calls, streaming)
                 → yt-dlp | ffmpeg → MP3 → Device
                 ↘ /prefetch/<id>  (background, warms next track)
                 ↘ yt-node-worker.js (persistent Node subprocess, optional)
```

| Component | Role |
|-----------|------|
| `Plugin.pm` | Menus, lifecycle, starts/stops the proxy |
| `API.pm` | Async HTTP calls from Perl to the local proxy |
| `ProtocolHandler.pm` | `ytm://` scheme — streaming, metadata |
| `PlaylistProtocolHandler.pm` | `ytmplaylist://` scheme — explodes a playlist/album into the queue |
| `Settings.pm` | Web settings page (path overrides, proxy port) |
| `ytmproxy.py` | Python sidecar — InnerTube API, yt-dlp/ffmpeg piping, prefetch, Node-worker lifecycle |
| `yt-node-worker.js` | Persistent Node subprocess, started by `ytmproxy.py` |

- **Two processes, not one, because YouTube's playback URLs require running
  YouTube's own JS.** `yt-dlp` needs to execute per-player-version JavaScript
  (the `n`/`sig` challenge solver, via the `yt-dlp-ejs` package's
  `lib.min.js`/`core.min.js`) to decrypt streaming URLs. `yt-node-worker.js`
  keeps a warmed V8 context per player version alive in a long-running Node
  process so each track doesn't pay a cold-start JS-eval cost; `ytmproxy.py`
  starts it (`_start_node_worker`), kills any stale instance first
  (`pgrep`/`taskkill` depending on OS), and talks to it over a Unix/named
  socket. It's optional — `--no-node-worker` disables it to save ~140MB RAM
  at the cost of slower per-track resolution.
- `ytmproxy.py` also pipes `yt-dlp`'s output through `ffmpeg` to produce a
  clean MP3 (falls back to FLAC on piCorePlayer, which lacks libmp3lame) —
  hardware Squeezebox decoders aren't reliable against raw YouTube CDN files.
- Unplayable-track replacement, prefetching, and search-history are all
  server-side state in `ytmproxy.py`, not in the Perl layer — Perl only
  renders menus and starts/stops the proxy.
- **If YouTube changes its API**, the only YouTube-specific code is in
  `ytmproxy.py`. Two constants near the top (`API_KEY`, `_CLIENT.clientVersion`)
  are the most likely things needing an update; if the *shape* of
  search/browse JSON changes, the parsing functions (`_parse_song`,
  `_shelf_items`, `_classify_and_parse`, etc.) need to follow. See README's
  "If the API changes" section for how to find current values.

### Operational notes (this deployment)

- The Lyrion/LMS server running this plugin lives at `10.0.0.104` on the home
  LAN: `:9000` is the Lyrion web UI, `:9876` is this plugin's proxy
  (`/ping`, `/ytdlp_status`, `/stream/<id>`, `/update_ytdlp`, ...).
- No SSH access to that host is set up yet, so its filesystem (e.g.
  `/var/lib/ytm-healthcheck/state`) and `systemctl` state can't be checked
  directly — only what the HTTP endpoints above expose.
- `tools/ytm-healthcheck.py` runs there via a systemd timer
  (`ytm-healthcheck.timer`, daily ~06:30 ± 20 min), not a general cron job: it
  streams 64 KB of a known song through the proxy, and on failure runs the
  proxy's yt-dlp update and retries, notifying Home Assistant via webhook
  (`broken` / `repaired` / `recovered` / `helper_down`). See `FORK.md`'s
  "Playback health check" section for install steps.

See README.md's "Troubleshooting" and "If the API changes" sections, and
FORK.md in full, before touching release mechanics or the InnerTube parsing
code — several of the above are the result of real production failures
(upstream's SHA-256-vs-SHA-1 repo.xml bug, the piCorePlayer MP3 fallback,
non-deterministic search ordering).
