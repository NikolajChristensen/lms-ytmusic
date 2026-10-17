#!/usr/bin/env python3
"""End-to-end menu/playback test of the YouTube Music plugin through LMS.

Runs on the Lyrion server (talks to http://127.0.0.1:9000/jsonrpc.js) and
exercises the plugin the way the different front ends do:

  browse  - `youtubemusic items ...`             (web UI, Material, apps)
  web     - `youtubemusic playlist play item_id` (web UI / Material taps)
  touch   - same plus touchToPlay=<id>           (players, Jivelite, apps)

It walks every top-level menu a few levels deep, keeps one representative of
each kind of playable item (per menu and type), and for each one checks on a
test player that the queue gets filled and that audio actually starts.

The test player is muted, its queue is saved first and put back at the end
(left stopped), and its power state is restored. A synced player is refused.

Usage:
  ytm-menu-test.py --player masterbedroom            # full run (~10-15 min)
  ytm-menu-test.py --player masterbedroom --browse-only
Options: --search TEXT (default: iotunn), --max-plays N (default: 18),
         --lms URL (default: http://127.0.0.1:9000)

Note: the typed-search test adds TEXT to the plugin's recent searches (moved
to the top if it is already there).
"""
import argparse
import json
import re
import sys
import time
import urllib.request

SKIP_NAMES = re.compile(r"remove|delete|clear|log ?out|sign ?out", re.I)


class LMS:
    def __init__(self, base):
        self.url = base.rstrip("/") + "/jsonrpc.js"

    def rpc(self, player, cmd, timeout=60):
        body = json.dumps({"id": 1, "method": "slim.request", "params": [player or "", cmd]}).encode()
        req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read()).get("result") or {}


def find_player(lms, wanted):
    res = lms.rpc("", ["players", 0, 99])
    want = re.sub(r"\W", "", wanted.lower())
    for p in res.get("players_loop", []):
        if want in re.sub(r"\W", "", p.get("name", "").lower()):
            return p["playerid"], p["name"]
    names = ", ".join(p.get("name", "?") for p in res.get("players_loop", []))
    sys.exit(f"Player '{wanted}' not found. Players: {names}")


# ── state backup / restore ──────────────────────────────────────────────────
def save_state(lms, pid):
    st = lms.rpc(pid, ["status", 0, 9999, "tags:u"])
    vol = lms.rpc(pid, ["mixer", "volume", "?"]).get("_volume", 0)
    return {
        "urls":  [t.get("url") for t in st.get("playlist_loop", []) if t.get("url")],
        "index": int(st.get("playlist_cur_index", 0) or 0),
        "power": int(st.get("power", 1) or 0),
        "volume": vol,
        "mode":  st.get("mode"),
    }


def restore_state(lms, pid, state):
    lms.rpc(pid, ["stop"])
    lms.rpc(pid, ["playlist", "clear"])
    for u in state["urls"]:
        lms.rpc(pid, ["playlist", "add", u])
    if state["urls"]:
        lms.rpc(pid, ["playlist", "index", state["index"]])
        lms.rpc(pid, ["stop"])
    lms.rpc(pid, ["mixer", "volume", state["volume"]])
    lms.rpc(pid, ["power", state["power"]])


# ── browsing ────────────────────────────────────────────────────────────────
def items(lms, pid, item_id=None, search=None):
    cmd = ["youtubemusic", "items", 0, 200]
    if item_id is not None:
        cmd.append(f"item_id:{item_id}")
    if search:
        cmd.append(f"search:{search}")
    t = time.time()
    res = lms.rpc(pid, cmd, timeout=120)
    return res.get("loop_loop", []), time.time() - t


def crawl(lms, pid, search, log):
    """Walk the menu tree; return (browse results, play candidates)."""
    browsed, candidates, seen_kinds = [], [], set()

    budget = [120]   # cap on menu requests, so a deep tree can't run for ages

    def visit(item_id, path, search_text, depth):
        if budget[0] <= 0:
            return
        budget[0] -= 1
        rows, dt = items(lms, pid, item_id, search_text)
        browsed.append({"path": " > ".join(path) or "(top)", "items": len(rows), "secs": round(dt, 1)})
        log(f"  browse {' > '.join(path) or '(top)':60} {len(rows):3} items  {dt:4.1f}s")
        if depth >= 4:
            return
        rows = [r for r in rows if not SKIP_NAMES.search(r.get("name", ""))]
        # A pure menu level (only submenus, few entries: the top level, My
        # Library, a search's Songs/Albums/... list) is opened entry by entry.
        # Content lists (albums, tracks, playlists) are reduced to one
        # representative per (type, playable, has-submenu), at most 4.
        menu_level = len(rows) <= 15 and not any(r.get("isaudio") for r in rows)
        if menu_level:
            reps = rows
        else:
            reps, kinds = [], set()
            for r in rows:
                k = (r.get("type"), r.get("isaudio"), r.get("hasitems"))
                if k not in kinds:
                    kinds.add(k)
                    reps.append(r)
                if len(reps) >= 4:
                    break
        for r in reps:
            name = (r.get("name") or "?")[:40]
            rpath = path + [name]
            if r.get("isaudio"):
                kind = (path[0] if path else name, len(rpath), r.get("type"), bool(r.get("hasitems")))
                if kind not in seen_kinds:
                    seen_kinds.add(kind)
                    candidates.append({"id": r["id"], "path": " > ".join(rpath), "type": r.get("type") or "-",
                                       "search": search_text})
            if r.get("type") == "search":
                visit(r["id"], rpath[:-1] + [f"{name} '{search}'"], search, depth + 1)
            elif r.get("hasitems") and r.get("type") not in ("audio", "text"):
                visit(r["id"], rpath, search_text, depth + 1)

    visit(None, [], None, 0)
    return browsed, candidates


# ── playing ─────────────────────────────────────────────────────────────────
def play_test(lms, pid, cand, route, wait=35):
    lms.rpc(pid, ["stop"])
    lms.rpc(pid, ["playlist", "clear"])
    cmd = ["youtubemusic", "playlist", "play", f"item_id:{cand['id']}"]
    if route == "touch":
        cmd.append(f"touchToPlay:{cand['id']}")
    if cand.get("search"):
        cmd.append(f"search:{cand['search']}")
    t0 = time.time()
    try:
        lms.rpc(pid, cmd, timeout=120)
    except Exception as e:
        return {"ok": False, "why": f"command failed: {e}"}
    tracks, started = 0, None
    while time.time() - t0 < wait:
        st = lms.rpc(pid, ["status", "-", 1, "tags:u"])
        tracks = int(st.get("playlist_tracks", 0) or 0)
        if st.get("mode") == "play" and float(st.get("time", 0) or 0) > 0.5:
            started = time.time() - t0
            cur = (st.get("playlist_loop") or [{}])[0].get("url", "")
            res = {"ok": True, "tracks": tracks, "index": int(st.get("playlist_cur_index", 0) or 0),
                   "start": round(started, 1), "url": cur[:24]}
            lms.rpc(pid, ["stop"])
            return res
        time.sleep(1)
    lms.rpc(pid, ["stop"])
    why = "nothing queued" if not tracks else f"{tracks} queued but no audio within {wait}s"
    return {"ok": False, "why": why, "tracks": tracks}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--player", required=True)
    ap.add_argument("--search", default="iotunn")
    ap.add_argument("--max-plays", type=int, default=18)
    ap.add_argument("--browse-only", action="store_true")
    ap.add_argument("--lms", default="http://127.0.0.1:9000")
    a = ap.parse_args()

    lms = LMS(a.lms)
    pid, pname = find_player(lms, a.player)
    sync = lms.rpc(pid, ["sync", "?"]).get("_sync", "-")
    if sync not in ("-", "", None) and not a.browse_only:
        sys.exit(f"'{pname}' is synced with other players ({sync}); unsync it first or use --browse-only.")

    report = []
    log = lambda s: (print(s, flush=True), report.append(s))
    log(f"YouTube Music menu test on '{pname}' ({pid}), search '{a.search}'")
    t_all = time.time()

    log("\n== Browsing (web UI / Material / apps route)")
    browsed, cands = crawl(lms, pid, a.search, log)
    empty = [b for b in browsed if b["items"] == 0]
    log(f"  {len(browsed)} menus opened, {len(empty)} empty, {len(cands)} kinds of playable item found")

    if not a.browse_only:
        state = save_state(lms, pid)
        log(f"\n== Playing ({min(len(cands), a.max_plays)} items x web + touch); "
            f"'{pname}' muted, its queue of {len(state['urls'])} tracks will be put back")
        lms.rpc(pid, ["power", 1])
        lms.rpc(pid, ["mixer", "volume", 0])
        fails = 0
        try:
            for c in cands[:a.max_plays]:
                for route in ("web", "touch"):
                    r = play_test(lms, pid, c, route)
                    if r["ok"]:
                        msg = f"OK   {r['tracks']:3} queued, #{r['index']}, audio after {r['start']}s"
                    else:
                        fails += 1
                        msg = f"FAIL {r['why']}"
                    log(f"  {route:5} {c['type']:8} {c['path'][:70]:70} {msg}")
        finally:
            restore_state(lms, pid, state)
            log(f"  '{pname}' restored: {len(state['urls'])} tracks, volume {state['volume']}, "
                f"power {'on' if state['power'] else 'off'} (left stopped)")
        log(f"  {fails} failed play test(s)")

    if empty:
        log("\n== Empty menus (worth a look)")
        for b in empty:
            log(f"  {b['path']}")
    log(f"\nDone in {time.time() - t_all:.0f}s")
    out = "/tmp/ytm-menu-test-report.txt"
    with open(out, "w") as f:
        f.write("\n".join(report) + "\n")
    print(f"(report also saved to {out})")


if __name__ == "__main__":
    main()
