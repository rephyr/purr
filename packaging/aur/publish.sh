#!/bin/sh
# Push the PKGBUILDs (and their .SRCINFO) to the AUR. Needs your AUR account with your SSH key
# (packaging/aur/README.md). The first push creates the package; after that `yay -S purr-agent` works.
#
#   packaging/aur/publish.sh                 both packages
#   packaging/aur/publish.sh purr-agent-git  one of them
set -eu
cd "$(dirname "$0")"
HERE=$(pwd)
for name in ${1:-purr-agent purr-agent-git}; do
    [ -f "$HERE/$name/.SRCINFO" ] || { echo "$name: no .SRCINFO yet: run packaging/aur/release.sh first" >&2; exit 1; }
    work=$(mktemp -d)
    git clone -q "ssh://aur@aur.archlinux.org/$name.git" "$work/$name"  # empty the first time: that's fine
    cp "$HERE/$name/PKGBUILD" "$HERE/$name/.SRCINFO" "$work/$name/"
    cd "$work/$name"
    git add PKGBUILD .SRCINFO
    if git diff --cached --quiet; then
        echo "$name: the AUR has this one already"
    else
        ver=$(sed -n 's/^\tpkgver = //p' .SRCINFO | head -1)
        rel=$(sed -n 's/^\tpkgrel = //p' .SRCINFO | head -1)
        git commit -qm "Update to $ver-$rel"
        git push -q origin HEAD:master
        echo "$name $ver-$rel is on the AUR ♡  (yay -S $name)"
    fi
    cd "$HERE"
    rm -rf "$work"
done
