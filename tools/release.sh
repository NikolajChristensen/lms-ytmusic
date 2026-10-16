#!/usr/bin/env bash
# Cut a release of this fork: bump the version, rebuild the zip, and rewrite
# repo.xml so its version, <url> and <sha> all agree with the artefact.
#
# These must move together - LMS downloads the zip named in <url> and refuses
# it unless its SHA-1 matches <sha>. (Upstream 1.4.6/1.4.7 shipped a SHA-256
# there, which can never match; see schmij97/lms-ytmusic#15.)
#
# Fork versions are upstream's version plus a fourth number: 1.4.7.1, 1.4.7.2,
# then 1.4.8.1 after merging upstream 1.4.8. They sort above the upstream
# release they're based on, so Lyrion offers them as updates.
#
#   tools/release.sh 1.4.7.1            # test + bump + build + rewrite, nothing published
#   tools/release.sh 1.4.7.1 --publish  # ...then commit, tag and create the GitHub release
#
# Edit <changes> in repo.xml by hand before releasing.
set -euo pipefail

cd "$(dirname "$0")/.."

VERSION="${1:-}"
PUBLISH="${2:-}"
REPO_SLUG="NikolajChristensen/lms-ytmusic"
PLUGIN_DIR="Plugins/YouTubeMusic"

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
	echo "usage: tools/release.sh <upstream-version>.<n> [--publish]   e.g. 1.4.7.1" >&2
	exit 2
fi

ZIP="dist/YouTubeMusic-${VERSION}.zip"
URL="https://github.com/${REPO_SLUG}/releases/download/v${VERSION}/YouTubeMusic-${VERSION}.zip"

echo "==> tests"
./tools/test-compile.sh

echo "==> version -> $VERSION"
perl -i -pe "s{<version>[^<]*</version>}{<version>${VERSION}</version>}" "$PLUGIN_DIR/install.xml"

echo "==> build $ZIP"
mkdir -p dist
rm -f dist/YouTubeMusic-*.zip
rm -rf "$PLUGIN_DIR/__pycache__"
# Same layout as upstream's zips: Plugins/YouTubeMusic/... at the root.
#
# Reproducible: the same tree always gives the same zip and SHA-1, so a dry
# run and --publish agree. Every entry's mtime is pinned to the last commit's
# time (in UTC, since zip stores local time), entries go in in sorted order,
# and -X drops uid/gid and platform extra fields.
EPOCH=$(git log -1 --format=%ct)
FILES=$(find "$PLUGIN_DIR" \( -name __pycache__ -o -name '*.pyc' -o -name '*.orig' -o -name '*.swp' \
	-o -name '*~' -o -name .DS_Store \) -prune -o -print | LC_ALL=C sort)
# shellcheck disable=SC2086
touch -h -d "@$EPOCH" $FILES
# shellcheck disable=SC2086
printf '%s\n' $FILES | TZ=UTC zip -qX -@ "$ZIP"
unzip -l "$ZIP"

SHA=$(sha1sum "$ZIP" | cut -d' ' -f1)
echo "==> sha1 $SHA"

echo "==> repo.xml"
perl -i -pe "
	s{<plugin name=\"YouTubeMusic\" version=\"[^\"]*\"}{<plugin name=\"YouTubeMusic\" version=\"${VERSION}\"};
	s{<url>[^<]*</url>}{<url>${URL}</url>};
	s{<sha>[^<]*</sha>}{<sha>${SHA}</sha>};
" repo.xml

# Fail loudly rather than ship a repo.xml that disagrees with the zip.
grep -q "<sha>${SHA}</sha>" repo.xml                         || { echo "repo.xml sha not updated" >&2; exit 1; }
[[ ${#SHA} -eq 40 ]]                                         || { echo "sha is not a SHA-1" >&2; exit 1; }
grep -q "version=\"${VERSION}\"" repo.xml                    || { echo "repo.xml version not updated" >&2; exit 1; }
grep -q "<url>${URL}</url>" repo.xml                         || { echo "repo.xml url not updated" >&2; exit 1; }
grep -q "<version>${VERSION}</version>" "$PLUGIN_DIR/install.xml" || { echo "install.xml not updated" >&2; exit 1; }
./tools/test-compile.sh >/dev/null                           || { echo "post-bump checks failed" >&2; exit 1; }

echo
echo "ready: $ZIP  ($SHA)"

if [[ "$PUBLISH" != "--publish" ]]; then
	echo "re-run with --publish to commit, tag v${VERSION} and create the GitHub release."
	exit 0
fi

echo "==> publishing v${VERSION}"
git add -A "$PLUGIN_DIR/install.xml" repo.xml
if git diff --cached --quiet; then
	echo "    (already committed, nothing to add)"
else
	git commit -m "Release ${VERSION}"
fi
git tag "v${VERSION}"
git push origin HEAD "v${VERSION}"
gh release create "v${VERSION}" "$ZIP" --repo "$REPO_SLUG" --title "YouTube Music ${VERSION}" --generate-notes
echo "done: https://github.com/${REPO_SLUG}/releases/tag/v${VERSION}"
