# About this fork

This is a fork of [schmij97/lms-ytmusic](https://github.com/schmij97/lms-ytmusic),
the YouTube Music plugin for Lyrion Music Server. It tracks upstream closely:
upstream releases are merged in, and fixes made here are also sent upstream as
pull requests. The fork exists so changes can be used before upstream ships
them, and so releases install reliably.

Fork notes live in this file rather than README.md, so merging upstream's
README never conflicts.

## Install

In Lyrion: **Settings → Plugins → Additional Repositories**, add

```
https://raw.githubusercontent.com/NikolajChristensen/lms-ytmusic/main/repo.xml
```

and remove schmij97's repository URL if it's listed. With both listed, Lyrion
offers whichever version is higher, so the installed plugin would flip between
the two. The plugin keeps upstream's name (`YouTubeMusic`), so settings and
favourites carry over in either direction.

## Versions

Upstream's version plus a fourth number: `1.4.7.1`, `1.4.7.2`, … then
`1.4.8.1` after merging upstream `1.4.8`. That always sorts above the upstream
release it's based on.

## Merging upstream

```
git fetch upstream
git merge upstream/main
```

`repo.xml` is marked `merge=ours` in `.gitattributes`, so upstream's copy
(URL, checksum, version) never replaces this fork's. That only works once per
clone after enabling the driver:

```
git config merge.ours.driver true
```

`Plugins/YouTubeMusic/install.xml` usually conflicts on the `<version>` line;
take either side, `tools/release.sh` rewrites it anyway.

## Releasing

```
tools/test-compile.sh                 # Perl syntax (Slim::* stubbed), Python, XML
tools/release.sh 1.4.7.1              # bump, build zip, rewrite repo.xml; nothing published
tools/release.sh 1.4.7.1 --publish    # ...then commit, tag, push and create the GitHub release
```

Edit `<changes>` in `repo.xml` by hand first. The script writes the zip's
SHA-1 into `<sha>` and refuses to finish if version, URL and checksum don't
agree.
