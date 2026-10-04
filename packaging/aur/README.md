# purr on the AUR

Two packages, so `yay -S purr-agent` (or any AUR helper) installs purr on Arch:

| package | what it builds | updates |
|---|---|---|
| `purr-agent` | a tagged release (`v0.5.0` on GitHub) | when you release (below) |
| `purr-agent-git` | the latest commit on main | by itself: `yay` rebuilds it from git |

**Not on the AUR yet:** AUR account sign-ups have been closed since June 2026 (a wave of malicious
packages); everything here is ready for the day they reopen. Meanwhile anyone can build the package:
`cd packaging/aur/purr-agent-git && makepkg -si`.

(`purr` itself is taken on the AUR, by a Catppuccin tool.) Both install purr into `/usr/lib/purr-agent`
with a `purr` launcher in `/usr/bin`, and depend only on `python` and `python-textual` (Arch's own
repos); Ollama, llama.cpp, git, gh, ripgrep, tmux, ruff are optional.

## Once: your AUR account

1. Make an account at <https://aur.archlinux.org/register> (use the same email as for GitHub if you like).
2. Add your SSH public key there (My Account → SSH Public Key). No key yet:
   `ssh-keygen -t ed25519 -f ~/.ssh/aur` and paste `~/.ssh/aur.pub`. Then tell ssh to use it for the AUR:
   ```
   Host aur.archlinux.org
       IdentityFile ~/.ssh/aur
       User aur
   ```
   in `~/.ssh/config`.
3. Check it: `ssh aur@aur.archlinux.org help` should answer (not "permission denied").

## Every release

See [../README.md](../README.md#a-release); `publish.sh` then pushes both packages to the AUR.

After that: `yay -S purr-agent` (or `purr-agent-git`). To try a PKGBUILD before publishing:
`cd packaging/aur/purr-agent && makepkg -si` builds and installs it (sudo asks for your password).
