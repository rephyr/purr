#!/bin/sh
# A purr release, ready for the AUR: tag it, make the GitHub release, and fill in purr-agent's
# PKGBUILD (version and checksum) and both .SRCINFO files. Then commit those (a PR) and run
# packaging/aur/publish.sh.
#
#   packaging/aur/release.sh 0.5.0      (harness/__init__.py must say VERSION = "0.5.0" already)
set -eu
cd "$(dirname "$0")/../.."
V="${1:?which version? like: packaging/aur/release.sh 0.5.0}"
HAVE=$(sed -n 's/^VERSION = "\([^"]*\)".*/\1/p' harness/__init__.py)
[ "$HAVE" = "$V" ] || { echo "harness/__init__.py says $HAVE, not $V: bump it (in a PR) first" >&2; exit 1; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "commit your changes first" >&2; exit 1; }
git fetch -q origin
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || {
    echo "release from an up-to-date main: git switch main && git pull" >&2; exit 1; }
if ! git rev-parse -q --verify "refs/tags/v$V" >/dev/null; then
    git tag -a "v$V" -m "purr $V"
    git push -q origin "v$V"
    gh release create "v$V" --title "purr $V" --generate-notes >/dev/null
    echo "tagged and released v$V"
fi
URL="https://github.com/rephyr/purr/archive/refs/tags/v$V.tar.gz"
SUM=$(curl -fsSL "$URL" | sha256sum | cut -d' ' -f1)
sed -i "s/^pkgver=.*/pkgver=$V/; s/^pkgrel=.*/pkgrel=1/; s/^sha256sums=.*/sha256sums=('$SUM')/" \
    packaging/aur/purr-agent/PKGBUILD
sed -i "s/^pkgver=.*/pkgver=$V.r0.g0000000/" packaging/aur/purr-agent-git/PKGBUILD
for name in purr-agent purr-agent-git; do
    (cd "packaging/aur/$name" && makepkg --printsrcinfo > .SRCINFO)
done
echo "PKGBUILDs ready for $V (sha256 $SUM): commit packaging/aur/, then packaging/aur/publish.sh"
