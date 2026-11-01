# Operational notes

- The Lyrion/LMS server running this plugin lives at `10.0.0.104` on the home
  LAN: `:9000` is the Lyrion web UI, `:9876` is the YouTubeMusic plugin's own
  helper (`/ping`, `/ytdlp_status`, `/stream/<id>`, `/update_ytdlp`, ...).
- No SSH access to that host is set up yet, so its filesystem (e.g.
  `/var/lib/ytm-healthcheck/state`) and `systemctl` state can't be checked
  directly — only what the HTTP endpoints above expose.
- `tools/ytm-healthcheck.py` runs there via a systemd timer
  (`ytm-healthcheck.timer`, daily ~06:30 ± 20 min), not a general cron job.
  See `FORK.md`'s "Playback health check" section for what it does and how
  it's installed.
