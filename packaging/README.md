# Getting purr to people

| where | install | how it's published |
|---|---|---|
| anywhere | `curl -LsSf https://raw.githubusercontent.com/rephyr/purr/main/install.sh \| sh` | always the latest main |
| PyPI | `uv tool install purr-agent` (or `pipx install purr-agent`) | every GitHub release, by `.github/workflows/publish.yml` |
| Arch (AUR) | `yay -S purr-agent` / `purr-agent-git` | [aur/](aur/): waiting for an account (AUR sign-ups are closed for now) |

## A release

1. Bump `VERSION` in `harness/__init__.py` (a PR, merged).
2. On an up-to-date main: `packaging/aur/release.sh 0.5.0`. It tags `v0.5.0` and makes the GitHub
   release (which publishes it to PyPI), and fills in purr-agent's PKGBUILD and both `.SRCINFO` files.
3. Commit `packaging/aur/` (a PR). Once there's an AUR account: `packaging/aur/publish.sh`.

## PyPI, once

PyPI needs to know this repository may publish `purr-agent` (trusted publishing: no token anywhere):

1. Make an account at <https://pypi.org/account/register/> (and turn on two-factor, PyPI asks for it).
2. <https://pypi.org/manage/account/publishing/> → **Add a new pending publisher**: PyPI project name
   `purr-agent`, owner `rephyr`, repository `purr`, workflow `publish.yml`, environment `pypi`.
3. That's it: the next release publishes. A release made before this can be published afterwards from
   GitHub: Actions → publish → Run workflow.
