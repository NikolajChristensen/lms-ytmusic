#!/usr/bin/env python3
"""Recreate a Spotify playlist in your YouTube Music account.

Input (one of):
  --csv FILE      Exportify CSV (exportify.net) - complete, includes ISRCs
  --links FILE    text file with open.spotify.com/track/... links, one per line
                  (Ctrl+A, Ctrl+C on the playlist in Spotify)
  --url URL       public playlist link - only the first 100 tracks

Each track is matched on YouTube Music: first by its ISRC (the exact
recording), then by artist + title, accepting a result only if title, artist
and duration agree. Nothing is changed in your account without --apply.

  ytm-import-playlist.py --csv list.csv                       # dry run, report
  ytm-import-playlist.py --csv list.csv --apply               # create playlist
  ytm-import-playlist.py --csv list.csv --apply --include-uncertain
Options: --name TEXT (default: the Spotify playlist's name), --save FILE /
         --matches FILE (save matches from a dry run / apply exactly those),
         --plugin-dir DIR (the YouTube Music plugin, for search and login)

--apply runs on the Lyrion server: it uses the login saved in the plugin's
settings and creates a PRIVATE playlist.
"""
import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.request

PLUGIN_DIR = "/var/lib/squeezeboxserver/cache/InstalledPlugins/Plugins/YouTubeMusic"


# ── reading Spotify ─────────────────────────────────────────────────────────
def _embed(kind, sid):
    req = urllib.request.Request(f"https://open.spotify.com/embed/{kind}/{sid}", headers={"User-Agent": "Mozilla/5.0"})
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))["props"]["pageProps"]["state"]["data"]["entity"]


def read_csv(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    name = os.path.splitext(os.path.basename(path))[0].replace("_", " ")
    return name, [{
        "title": r["Track Name"], "artists": [a.strip() for a in r["Artist Name(s)"].split(";") if a.strip()],
        "album": r.get("Album Name", ""), "duration": round(int(r["Duration (ms)"] or 0) / 1000),
        "isrc": r.get("ISRC", ""),
    } for r in rows if r.get("Track Name")]


def read_links(path):
    ids = re.findall(r"open\.spotify\.com/track/([A-Za-z0-9]+)", open(path).read())
    tracks = []
    for i, tid in enumerate(dict.fromkeys(ids), 1):
        e = _embed("track", tid)
        tracks.append({"title": e.get("name") or e.get("title", ""), "artists": [a["name"] for a in e.get("artists", [])],
                       "album": "", "duration": round((e.get("duration") or 0) / 1000), "isrc": ""})
        print(f"\r  reading Spotify tracks {i}/{len(ids)}", end="", flush=True)
        time.sleep(0.2)
    print()
    return None, tracks


def read_url(url):
    pid = re.search(r"playlist/([A-Za-z0-9]+)", url).group(1)
    e = _embed("playlist", pid)
    return e.get("name"), [{"title": t["title"], "artists": [a.strip() for a in t["subtitle"].split(",")],
                            "album": "", "duration": round((t.get("duration") or 0) / 1000), "isrc": ""}
                           for t in e.get("trackList", [])]


# ── matching ────────────────────────────────────────────────────────────────
def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _words(s):
    s = re.sub(r"\(|\)|\[|\]", " ", s or "")           # "(Version Two)" == "- Version Two"
    return {w for w in _norm(s).split() if len(w) > 2 and w not in ("from", "the", "and", "soundtrack", "version", "remastered", "original")}


def _score(track, r):
    tw, rw = _words(track["title"]), _words(r.get("title"))
    # Share of the shorter title's words found in the other: "3 Latin American
    # Sketches: Paisaje Mexicano" vs "Paisaje Mexicano" is a full match.
    title = len(tw & rw) / max(1, min(len(tw), len(rw))) if tw and rw else float(_norm(track["title"]) == _norm(r.get("title")))
    dd = abs((r.get("duration") or 0) - track["duration"])
    ra = _norm(r.get("artist"))
    artist = any(_norm(a) and (_norm(a) in ra or (ra and ra in _norm(a))) for a in track["artists"])
    album = bool(track["album"]) and _norm(track["album"])[:20] in _norm(r.get("album"))
    return title, dd, artist, album


def match(search, track):
    """('good'|'uncertain'|'missing', result, how)"""
    if track.get("isrc"):
        for r in [x for x in search(track["isrc"], "songs") if x.get("type") == "song" and x.get("videoId")][:3]:
            title, dd, artist, _ = _score(track, r)
            if title >= 0.5 and dd <= 3:
                return "good", r, "ISRC"
    q = f'{track["artists"][0] if track["artists"] else ""} {track["title"]}'.strip()
    best = None
    for r in [x for x in search(q, "songs") if x.get("type") == "song" and x.get("videoId")][:10]:
        title, dd, artist, album = _score(track, r)
        rank = (title >= 0.6) + (dd <= 3) + artist + 0.5 * album - min(dd, 60) / 600
        if best is None or rank > best[0]:
            best = (rank, r, title, dd, artist)
    if best:
        rank, r, title, dd, artist = best
        if title >= 0.6 and dd <= 3 and artist:
            return "good", r, "search"
        if title >= 0.6 and (dd <= 15 or artist):
            return "uncertain", r, f"search, {dd:+.0f}s" if dd else "search, other artist"
    return "missing", None, ""


# ── YouTube Music side ──────────────────────────────────────────────────────
def load_plugin(plugin_dir):
    sys.path.insert(0, plugin_dir)
    import ytmproxy
    if ytmproxy.BIN_DIR not in sys.path:
        sys.path.insert(0, ytmproxy.BIN_DIR)
    return ytmproxy


def signed_in(ytmproxy, lms="http://127.0.0.1:9000"):
    body = json.dumps({"id": 1, "method": "slim.request", "params": ["", ["pref", "plugin.youtubemusic:ytm_cookie", "?"]]}).encode()
    req = urllib.request.Request(lms + "/jsonrpc.js", data=body, headers={"Content-Type": "application/json"})
    raw = (json.loads(urllib.request.urlopen(req, timeout=30).read()).get("result") or {}).get("_p2") or ""
    if not raw.strip():
        sys.exit("No YouTube Music headers saved in the plugin settings.")
    from ytmusicapi import YTMusic, setup
    return YTMusic(setup(headers_raw=ytmproxy._normalize_pasted_headers(raw)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv"); src.add_argument("--links"); src.add_argument("--url"); src.add_argument("--matches")
    ap.add_argument("--name")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--include-uncertain", action="store_true")
    ap.add_argument("--save")
    ap.add_argument("--plugin-dir", default=PLUGIN_DIR)
    a = ap.parse_args()
    ytmproxy = load_plugin(a.plugin_dir)

    if a.matches:
        saved = json.load(open(a.matches))
        name, results = saved["name"], saved["results"]
    else:
        name, tracks = read_csv(a.csv) if a.csv else read_links(a.links) if a.links else read_url(a.url)
        name = a.name or name or "Imported from Spotify"
        print(f"Spotify playlist '{name}': {len(tracks)} tracks. Matching on YouTube Music...")
        results = []
        for i, t in enumerate(tracks, 1):
            status, r, how = match(ytmproxy.search, t)
            results.append({"spotify": t, "status": status, "how": how,
                            "ytm": {k: r.get(k) for k in ("videoId", "title", "artist", "album", "duration")} if r else None})
            print(f"\r  {i}/{len(tracks)}", end="", flush=True)
        print()
        if a.save:
            json.dump({"name": name, "results": results}, open(a.save, "w"), ensure_ascii=False, indent=1)
            print(f"  matches saved to {a.save}")
    name = a.name or name

    count = {s: sum(1 for r in results if r["status"] == s) for s in ("good", "uncertain", "missing")}
    print(f"\n{count['good']} reliable, {count['uncertain']} uncertain, {count['missing']} not found")
    if count["uncertain"]:
        print("\nUncertain (Spotify  ->  YouTube Music):")
        for r in results:
            if r["status"] == "uncertain":
                s, y = r["spotify"], r["ytm"]
                print(f"  ? {s['title'][:42]:42} {s['duration']:4}s  ->  {y['title'][:36]:36} | {y['artist'][:18]:18} {y['duration']:4}s  ({r['how']})")
    if count["missing"]:
        print("\nNot found:")
        for r in results:
            if r["status"] == "missing":
                s = r["spotify"]
                print(f"  x {s['title'][:60]} - {', '.join(s['artists'])[:40]}")

    take = [r for r in results if r["status"] == "good" or (a.include_uncertain and r["status"] == "uncertain")]
    if not a.apply:
        print(f"\nDry run: would create private playlist '{name}' with {len(take)} tracks"
              f"{' (add --include-uncertain to also take the uncertain ones)' if count['uncertain'] and not a.include_uncertain else ''}."
              " Nothing changed.")
        return

    yt = signed_in(ytmproxy)
    ids = list(dict.fromkeys(r["ytm"]["videoId"] for r in take))
    pid = yt.create_playlist(name, "Imported from Spotify", privacy_status="PRIVATE")
    if not isinstance(pid, str):
        sys.exit(f"Creating the playlist failed: {pid}")
    for i in range(0, len(ids), 50):                       # add in batches, in order
        res = yt.add_playlist_items(pid, ids[i:i + 50], duplicates=True)
        if not (isinstance(res, dict) and "SUCCEEDED" in str(res.get("status", ""))):
            sys.exit(f"Adding tracks failed after {i}: {res}")
    print(f"\nCreated private playlist '{name}' with {len(ids)} tracks: https://music.youtube.com/playlist?list={pid}")
    print("(It appears under My Library > My Playlists in Lyrion within 10 minutes.)")


if __name__ == "__main__":
    main()
