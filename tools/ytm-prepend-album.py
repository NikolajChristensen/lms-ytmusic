#!/usr/bin/env python3
"""Put an album's tracks at the start of one of your YouTube Music playlists.

Runs on the Lyrion server, using the login (request headers) saved in the
YouTube Music plugin's settings and the ytmusicapi copy the plugin installed,
so the cookie never leaves the server. Nothing is changed without --apply.

  ytm-prepend-album.py --playlist "Somni - and encores" --album "Somni"
  ytm-prepend-album.py --playlist "Somni - and encores" --album "Somni" --apply

The album is looked up in your library first, then in search. Tracks already
in the playlist are not added twice. The new tracks go, in album order, before
the playlist's current first track.
"""
import argparse
import json
import os
import re
import sys
import urllib.request

PLUGIN_DIR = "/var/lib/squeezeboxserver/cache/InstalledPlugins/Plugins/YouTubeMusic"


def lms_pref(name, lms="http://127.0.0.1:9000"):
    body = json.dumps({"id": 1, "method": "slim.request", "params": ["", ["pref", name, "?"]]}).encode()
    req = urllib.request.Request(lms + "/jsonrpc.js", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return (json.loads(r.read()).get("result") or {}).get("_p2") or ""


def norm(s):
    return re.sub(r"[\s‐-―-]+", " ", (s or "").lower()).strip()


def connect(plugin_dir):
    sys.path.insert(0, plugin_dir)
    import ytmproxy                                   # the plugin's helper module
    if ytmproxy.BIN_DIR not in sys.path:
        sys.path.insert(0, ytmproxy.BIN_DIR)
    from ytmusicapi import YTMusic, setup
    raw = lms_pref("plugin.youtubemusic:ytm_cookie")
    if not raw.strip():
        sys.exit("No YouTube Music headers saved in the plugin settings.")
    return YTMusic(setup(headers_raw=ytmproxy._normalize_pasted_headers(raw)))


def find_playlist(yt, title):
    pls = yt.get_library_playlists(limit=None)
    hits = [p for p in pls if norm(p.get("title")) == norm(title)]
    if not hits:
        sys.exit(f"No playlist called '{title}' in your library. Yours: "
                 + ", ".join(p.get("title", "?") for p in pls[:30]))
    if len(hits) > 1:
        sys.exit(f"{len(hits)} playlists are called '{title}'; rename one first.")
    return hits[0]["playlistId"]


def find_album(yt, title):
    albums = [a for a in yt.get_library_albums(limit=None) if norm(a.get("title")) == norm(title)]
    if not albums:
        albums = [a for a in yt.search(title, filter="albums") if norm(a.get("title")) == norm(title)]
    if not albums:
        sys.exit(f"No album called '{title}' found in your library or in search.")
    return yt.get_album(albums[0]["browseId"])


def show(label, tracks):
    print(label)
    for i, t in enumerate(tracks, 1):
        print(f"  {i:2}. {t.get('title')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--playlist", required=True)
    ap.add_argument("--album", required=True)
    ap.add_argument("--apply", action="store_true", help="actually change the playlist")
    ap.add_argument("--plugin-dir", default=PLUGIN_DIR)
    a = ap.parse_args()

    yt = connect(a.plugin_dir)
    pid = find_playlist(yt, a.playlist)
    pl = yt.get_playlist(pid, limit=None)
    album = find_album(yt, a.album)
    artists = ", ".join(x.get("name", "") for x in album.get("artists") or [])

    current = pl.get("tracks") or []
    have = {t.get("videoId") for t in current}
    album_tracks = [t for t in album.get("tracks") or [] if t.get("videoId")]
    skipped = [t for t in album.get("tracks") or [] if not t.get("videoId")]
    new = [t for t in album_tracks if t["videoId"] not in have]

    print(f"Playlist: {pl.get('title')}  ({len(current)} tracks)")
    print(f"Album:    {album.get('title')} - {artists} ({album.get('year', '')}), {len(album_tracks)} playable tracks")
    if skipped:
        print(f"          {len(skipped)} album track(s) not available on YouTube Music, left out")
    show("\nPlaylist now:", current)
    if not new:
        print("\nAll album tracks are already in the playlist; nothing to do.")
        return
    show("\nWould become:", new + current)

    if not a.apply:
        print("\nDry run - nothing changed. Run again with --apply to do it.")
        return

    status = yt.add_playlist_items(pid, [t["videoId"] for t in new], duplicates=False)
    if not (isinstance(status, dict) and "SUCCEEDED" in str(status.get("status", ""))):
        sys.exit(f"Adding failed: {status}")

    # Re-read to get each entry's setVideoId, then move the new ones, in album
    # order, in front of the playlist's previous first track.
    after = yt.get_playlist(pid, limit=None).get("tracks") or []
    if current:
        anchor = next((t["setVideoId"] for t in after if t.get("videoId") == current[0].get("videoId")), None)
        new_ids = {t["videoId"] for t in new}
        for t in new:
            entry = next((x for x in after if x.get("videoId") == t["videoId"] and x.get("videoId") in new_ids), None)
            if entry and anchor:
                yt.edit_playlist(pid, moveItem=(entry["setVideoId"], anchor))
    final = yt.get_playlist(pid, limit=None).get("tracks") or []
    show("\nDone. Playlist is now:", final)
    print("\n(Lyrion's My Library may show the old order for up to 10 minutes.)")


if __name__ == "__main__":
    main()
