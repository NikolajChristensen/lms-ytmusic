#!/usr/bin/env python3
"""Follow (subscribe to) your Spotify artists on YouTube Music.

Input (one or both):
  --library FILE   YourLibrary.json from Spotify's "Account data" download:
                   the artists you follow there
  --history DIR    "Spotify Extended Streaming History" folder: artists you
                   have played at least --min-plays times (default 25; a play
                   counts from 30 seconds)

Each artist is looked up on YouTube Music. When your history has songs by the
artist, the artist of your most played song is looked up too, which tells
same-named artists apart; otherwise only an exact name match counts as
reliable. Artists you already follow are left alone. Nothing is changed in
your account without --apply.

  ytm-follow-artists.py --history DIR --save artists.json     # dry run, report
  ytm-follow-artists.py --matches artists.json --apply         # follow them
Options: --include-uncertain, --min-plays N, --plugin-dir DIR

--apply runs on the Lyrion server: it uses the login saved in the plugin's
settings. The dry run works anywhere the plugin's ytmproxy.py is (it only
searches), so the matching can be done on a PC and the --save file copied
to the server.
"""
import argparse
import collections
import glob
import json
import os
import re
import sys
import unicodedata

PLUGIN_DIR = "/var/lib/squeezeboxserver/cache/InstalledPlugins/Plugins/YouTubeMusic"


# ── reading Spotify ─────────────────────────────────────────────────────────
def read_library(path):
    data = json.load(open(path, encoding="utf-8"))
    return [a["name"] for a in data.get("artists", []) if a.get("name")]


def read_history(folder):
    """{artist: {"plays": n, "tracks": [most played first]}}"""
    plays, tracks = collections.Counter(), collections.defaultdict(collections.Counter)
    files = glob.glob(os.path.join(folder, "Streaming_History_Audio_*.json"))
    if not files:
        sys.exit(f"No Streaming_History_Audio_*.json files in {folder}")
    for f in files:
        for e in json.load(open(f, encoding="utf-8")):
            a, t = e.get("master_metadata_album_artist_name"), e.get("master_metadata_track_name")
            if a and t and e.get("ms_played", 0) >= 30000:
                plays[a] += 1
                tracks[a][t] += 1
    return {a: {"plays": n, "tracks": [t for t, _ in tracks[a].most_common(3)]} for a, n in plays.items()}


# ── matching ────────────────────────────────────────────────────────────────
def _norm(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"^the\s+", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s.replace("&", " and ")).strip()


def match(ytm, name, tracks):
    """('good'|'uncertain'|'missing', {name, browseId}, how)"""
    want = _norm(name)
    found = [r for r in ytm.search(name, "artists") if r.get("type") == "artist" and r.get("browseId")]
    exact = [r for r in found if _norm(r.get("name")) == want]

    # The artist of a song you played: the strongest evidence there is.
    via_song = None
    for title in tracks[:2]:
        for s in ytm.search(f"{name} {title}", "songs")[:5]:
            if s.get("videoId") and want in _norm(s.get("artist")):
                for a in ytm.get_song_links(s["videoId"]).get("artists", []):
                    if _norm(a["name"]) == want:
                        via_song = a
                        break
            if via_song:
                break
        if via_song:
            break

    if via_song:
        how = "name + song" if exact and exact[0]["browseId"] == via_song["browseId"] else "song"
        return "good", {"name": via_song["name"], "browseId": via_song["browseId"]}, how
    if len(exact) == 1 or (exact and not tracks):
        r = exact[0]
        return ("good" if len(exact) == 1 else "uncertain"), {"name": r["name"], "browseId": r["browseId"]}, \
               "name" if len(exact) == 1 else f"{len(exact)} artists with this name"
    if exact:
        r = exact[0]
        return "uncertain", {"name": r["name"], "browseId": r["browseId"]}, f"{len(exact)} artists with this name"
    if found and (want in _norm(found[0].get("name")) or _norm(found[0].get("name")) in want):
        r = found[0]
        return "uncertain", {"name": r["name"], "browseId": r["browseId"]}, "similar name"
    return "missing", None, ""


# ── YouTube Music side ──────────────────────────────────────────────────────
def load_plugin(plugin_dir):
    sys.path.insert(0, plugin_dir)
    import ytmproxy
    if ytmproxy.BIN_DIR not in sys.path:
        sys.path.insert(0, ytmproxy.BIN_DIR)
    return ytmproxy


def signed_in(ytmproxy, lms="http://127.0.0.1:9000"):
    import urllib.request
    body = json.dumps({"id": 1, "method": "slim.request", "params": ["", ["pref", "plugin.youtubemusic:ytm_cookie", "?"]]}).encode()
    req = urllib.request.Request(lms + "/jsonrpc.js", data=body, headers={"Content-Type": "application/json"})
    raw = (json.loads(urllib.request.urlopen(req, timeout=30).read()).get("result") or {}).get("_p2") or ""
    if not raw.strip():
        sys.exit("No YouTube Music headers saved in the plugin settings.")
    from ytmusicapi import YTMusic, setup
    return YTMusic(setup(headers_raw=ytmproxy._normalize_pasted_headers(raw)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--library")
    ap.add_argument("--history")
    ap.add_argument("--matches")
    ap.add_argument("--min-plays", type=int, default=25)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--include-uncertain", action="store_true")
    ap.add_argument("--save")
    ap.add_argument("--plugin-dir", default=PLUGIN_DIR)
    a = ap.parse_args()
    if not (a.library or a.history or a.matches):
        ap.error("give --library, --history or --matches")
    ytm = load_plugin(a.plugin_dir)

    if a.matches:
        results = json.load(open(a.matches, encoding="utf-8"))
    else:
        history = read_history(a.history) if a.history else {}
        names = read_library(a.library) if a.library else []
        names += sorted((n for n, h in history.items() if h["plays"] >= a.min_plays and n not in names),
                        key=lambda n: -history[n]["plays"])
        print(f"{len(names)} artists. Looking them up on YouTube Music...")
        results = []
        for i, name in enumerate(names, 1):
            h = history.get(name, {})
            try:
                status, r, how = match(ytm, name, h.get("tracks", []))
            except Exception as e:
                status, r, how = "missing", None, f"error: {e}"
            results.append({"spotify": name, "plays": h.get("plays", 0), "status": status, "how": how, "ytm": r})
            print(f"\r  {i}/{len(names)}", end="", flush=True)
        print()
        if a.save:
            json.dump(results, open(a.save, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"  matches saved to {a.save}")

    count = {s: sum(1 for r in results if r["status"] == s) for s in ("good", "uncertain", "missing")}
    print(f"\n{count['good']} reliable, {count['uncertain']} uncertain, {count['missing']} not found")
    for status, mark, label in (("uncertain", "?", "Uncertain (Spotify  ->  YouTube Music)"), ("missing", "x", "Not found")):
        rows = [r for r in results if r["status"] == status]
        if rows:
            print(f"\n{label}:")
            for r in rows:
                y = r["ytm"]
                print(f"  {mark} {r['spotify'][:40]:40}" + (f"  ->  {y['name'][:30]:30} ({r['how']})" if y else
                                                          (f"  ({r['how']})" if r["how"] else "")))

    take = [r for r in results if r["status"] == "good" or (a.include_uncertain and r["status"] == "uncertain")]
    ids = list(dict.fromkeys(r["ytm"]["browseId"] for r in take))
    if not a.apply:
        print(f"\nDry run: would follow {len(ids)} artists"
              f"{' (add --include-uncertain to also take the uncertain ones)' if count['uncertain'] and not a.include_uncertain else ''}."
              " Nothing changed.")
        return

    yt = signed_in(ytm)
    have = {s.get("browseId") for s in yt.get_library_subscriptions(limit=None)}
    new = [i for i in ids if i not in have]
    print(f"\nAlready following {len(ids) - len(new)} of them; following {len(new)} more...")
    for i in range(0, len(new), 50):
        yt.subscribe_artists(new[i:i + 50])
    print(f"Done. They appear under My Library > Artists in Lyrion within 10 minutes.")


if __name__ == "__main__":
    main()
