#!/bin/sh
# purr in one line:  curl -LsSf https://raw.githubusercontent.com/rephyr/purr/main/install.sh | sh
# Installs uv (if you don't have it) and then purr with it, in its own little Python of its own:
# nothing touches your system's Python. On Arch: `yay -S purr-agent` instead.
# PURR_REF=<branch or tag> installs that one instead of main.
set -eu
say() { printf '\033[38;2;245;169;208m%s\033[0m\n' "$1"; }
dim() { printf '\033[38;2;130;120;145m%s\033[0m\n' "$1"; }

say "₊˚✧ installing purr ✧˚₊"
if ! command -v uv >/dev/null 2>&1; then
    dim "getting uv first (it installs purr and the Python it runs on)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    PATH="$HOME/.local/bin:$PATH"
fi
uv tool install --force --python 3.12 "git+https://github.com/rephyr/purr${PURR_REF:+@$PURR_REF}"
if ! command -v purr >/dev/null 2>&1; then
    uv tool update-shell >/dev/null 2>&1 || true
    dim "open a new terminal so it finds the purr command (uv added its folder to your PATH)"
fi
say "done ♡  run purr in a project folder: the first start finds your models and sets you up"
