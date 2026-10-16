#!/usr/bin/env python3
"""Daily end-to-end check that YouTube Music playback works in Lyrion.

Streams the first 64 KB of a known song through the plugin's own helper
(http://127.0.0.1:<port>/stream/<id>) - the same yt-dlp, Node worker and route
real playback uses. If that fails it runs the helper's "Update yt-dlp" and
tests again, then notifies Home Assistant through a webhook:

  broken      - still failing after the update (repeated daily while broken)
  repaired    - failing, but the yt-dlp update fixed it
  recovered   - working again after an earlier "broken"
  helper_down - the plugin's helper isn't answering at all

Configuration: /etc/ytm-healthcheck.conf (KEY=value lines)
  HA_WEBHOOK_URL=http://homeassistant.local:8123/api/webhook/<id>   (required)
  YTM_PORT=9876                                                     (optional)

Run by ytm-healthcheck.timer; `ytm-healthcheck.py --test-notify` sends a
test notification, `--dry-run` checks without updating or notifying.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

CONF = os.environ.get("YTM_HC_CONF", "/etc/ytm-healthcheck.conf")
STATE = os.environ.get("YTM_HC_STATE", "/var/lib/ytm-healthcheck/state")

# Official audio tracks known to play; one per day, so the helper's
# 10-minute prefetch cache never answers for a previous run.
SONGS = (
    "DVOImtJY7po",   # Metallica - Ride The Lightning
    "0fUDPHOQI58",   # Tina Dico - Losing
    "8zSODUOoE8w",   # Metallica - Fight Fire With Fire
    "UdNdfzZZxpQ",   # Tina Dico - Warm Sand
    "B_HSa1dEL9s",   # Metallica - For Whom The Bell Tolls
    "K0pKc935Y98",   # Katy Perry - The One That Got Away
)


def log(msg):
    print(msg, flush=True)


def load_conf():
    conf = {}
    try:
        with open(CONF) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    conf[k.strip()] = v.strip().strip('"')
    except FileNotFoundError:
        pass
    return conf


def helper(port, path, timeout):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
        return json.loads(r.read())


def ytdlp_version(port):
    try:
        return helper(port, "/ytdlp_status", 40).get("version") or "unknown"
    except Exception:
        return "unknown"


def stream_ok(port, video_id):
    """True if the helper delivers 64 KB of audio for video_id within 120 s."""
    req = urllib.request.Request(f"http://127.0.0.1:{port}/stream/{video_id}",
                                 headers={"Range": "bytes=0-"})
    got, deadline = 0, time.time() + 120
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            while got < 65536 and time.time() < deadline:
                chunk = r.read(16384)
                if not chunk:
                    break
                got += len(chunk)
    except Exception as e:
        log(f"stream {video_id}: {e}")
        return False
    log(f"stream {video_id}: {got} bytes")
    return got >= 65536


def notify(conf, kind, title, message):
    url = conf.get("HA_WEBHOOK_URL")
    if not url:
        log("HA_WEBHOOK_URL not set in " + CONF + "; not notifying")
        return
    body = json.dumps({"kind": kind, "title": title, "message": message}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
        log(f"notified: {kind}")
    except Exception as e:
        log(f"notify failed: {e}")


def read_state():
    try:
        with open(STATE) as f:
            return f.read().strip()
    except FileNotFoundError:
        return "ok"


def write_state(s):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        f.write(s + "\n")


def main():
    conf = load_conf()
    port = int(conf.get("YTM_PORT", "9876"))
    dry = "--dry-run" in sys.argv

    if "--test-notify" in sys.argv:
        notify(conf, "test", "YouTube Music check",
               "Test notification from the Lyrion YouTube Music health check.")
        return 0

    try:
        helper(port, "/ping", 10)
    except Exception as e:
        log(f"helper not answering: {e}")
        if not dry:
            notify(conf, "helper_down", "YouTube Music: helper not running",
                   "The YouTube Music plugin's helper in Lyrion isn't answering. "
                   "Restarting Lyrion usually brings it back.")
        return 1

    video = SONGS[time.localtime().tm_yday % len(SONGS)]
    before = read_state()

    if stream_ok(port, video):
        log("ok")
        if before == "broken" and not dry:
            notify(conf, "recovered", "YouTube Music works again",
                   f"Songs stream again in Lyrion (yt-dlp {ytdlp_version(port)}).")
        if not dry:
            write_state("ok")
        return 0

    if dry:
        log("failed (dry run: not updating or notifying)")
        return 1

    old = ytdlp_version(port)
    log(f"failed with yt-dlp {old}; updating")
    try:
        res = helper(port, "/update_ytdlp", 600)
        log(f"update: {res}")
    except Exception as e:
        log(f"update failed: {e}")
    time.sleep(15)
    new = ytdlp_version(port)

    alt = SONGS[(time.localtime().tm_yday + 1) % len(SONGS)]
    if stream_ok(port, video) or stream_ok(port, alt):
        notify(conf, "repaired", "YouTube Music repaired itself",
               f"Songs had stopped streaming in Lyrion; updating yt-dlp "
               f"({old} → {new}) fixed it.")
        write_state("ok")
        return 0

    notify(conf, "broken", "YouTube Music is broken",
           f"Songs can't be streamed in Lyrion, even after updating yt-dlp "
           f"(now {new}). Usually YouTube changed something and a yt-dlp or "
           f"plugin update follows within days. Restarting Lyrion may also help.")
    write_state("broken")
    return 1


if __name__ == "__main__":
    sys.exit(main())
