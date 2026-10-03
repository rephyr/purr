"""/pr: turn the changes into a GitHub pull request.

The model drafts a title and description from the diff and the chat; you check and edit them;
only then does purr make a branch, commit (with "Co-Authored-By: purr-<model>"), push and run
`gh pr create`. Nothing leaves your machine before you say yes.
"""

import re
import subprocess

DRAFT = """Write a GitHub pull request for these changes.

What the user asked for in this chat:
{asks}

Changed files:
{files}

The diff:
{diff}

Reply in exactly this form and nothing else:
TITLE: a short title in the imperative, under 70 characters (like "Fix happy hour discount")
BODY:
2 to 6 short lines: what changed and why, as a "- " list. Only say it was tested if the chat
shows tests being run; never claim checks that didn't happen."""

CO_AUTHOR_EMAIL = "purr@users.noreply.github.com"  # config.toml co_author_email overrides it


def git(root, *args, check=False):
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=60)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.strip()


def changed_files(root):
    """['M harness/agent.py', '?? new.py', ...] or None outside a git repo."""
    if git(root, "rev-parse", "--is-inside-work-tree") != "true":
        return None
    return [line for line in git(root, "status", "--porcelain").splitlines() if line.strip()]


def draft(agent):
    """(title, body) written by the current model from the diff and your requests."""
    root = agent.root
    files = changed_files(root) or []
    diff = git(root, "diff", "HEAD") or git(root, "diff")
    for line in files:  # new files aren't in git diff: show their start
        if line.startswith("??"):
            path = line[3:].strip()
            try:
                diff += f"\n--- new file {path}\n" + (root / path).read_text(errors="replace")[:1500]
            except (OSError, IsADirectoryError):
                pass
    asks = [m["content"] for m in agent.messages if m.get("role") == "user"
            and isinstance(m.get("content"), str) and not m["content"].startswith("(")]
    prompt = DRAFT.format(asks="\n".join(f"- {a[:300]}" for a in asks[-4:]) or "- (nothing said)",
                          files="\n".join(files), diff=diff[:12000])
    agent.view.activity("refining", "writing your pull request")
    reply = agent._call([{"role": "user", "content": prompt}], tools=False, quiet=True)
    agent._count(reply["usage"])
    text = reply["text"].strip()
    m = re.search(r"TITLE:\s*(.+)", text)
    title = (m.group(1).strip().strip('"') if m else text.splitlines()[0] if text else "Changes from purr")[:100]
    body = text.split("BODY:", 1)[1].strip() if "BODY:" in text else text
    return with_model(title, agent.model_name), body


def with_model(title, model):
    """"Fix the discount · qwen3-coder-32k": with squash merging the PR title becomes the commit
    title on main, so the model shows in GitHub's top bar (the avatar can only say purr-harness)."""
    return title if title.endswith(f"· {model}") else f"{title} · {model}"


def label(root, model):
    """A pink "🐾 <model>" label for the PR (made the first time). None if GitHub says no."""
    name = f"🐾 {model}"[:50]
    r = subprocess.run(["gh", "label", "create", name, "--color", "f5a9d0", "--force",
                        "--description", f"made by purr with {model}"[:100]],
                       cwd=root, capture_output=True, text=True, timeout=60)
    return name if r.returncode == 0 else None


def slug(title):
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return "purr/" + (s[:40].rstrip("-") or "changes")


def create(agent, title, body):
    """Branch, commit, push, open the PR. Returns the PR's URL. Raises RuntimeError on failure."""
    return open_pr(agent.root, agent.config, agent.model_name, title, body)


def open_pr(root, config, model, title, body, new_branch=False, back=False):
    """Everything not committed in root into a pull request, co-authored by purr-<model>. Returns
    its URL; raises RuntimeError on failure. new_branch: a branch of its own even off a feature
    branch (a benchmark report); back: then return to the branch you were on, so the checkout is
    clean again (the next benchmark run refuses uncommitted changes)."""
    if not changed_files(root):
        raise RuntimeError("nothing to commit")
    start = branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    default = git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").removeprefix("origin/") or "main"
    if new_branch or branch in (default, "main", "master", "HEAD"):  # never commit straight onto main
        branch = slug(title)
        if git(root, "rev-parse", "--verify", "--quiet", branch):
            branch += "-2"
        git(root, "checkout", "-b", branch, check=True)
    try:
        # GitHub shows a co-author's avatar when the email belongs to an account: give purr its own
        # account with a cute picture and put its noreply email in config.toml (co_author_email)
        email = config.get("co_author_email", CO_AUTHOR_EMAIL)
        co_author = f"Co-Authored-By: purr-{model} <{email}>"
        title = with_model(title, model)
        git(root, "add", "-A", check=True)
        git(root, "commit", "-m", title, "-m", body, "-m", f"Model: {model}\n{co_author}", check=True)
        git(root, "push", "-u", "origin", branch, check=True)
        badge = config.get("co_author_github")  # its picture in the PR, if it has an account
        pic = f'<img src="https://github.com/{badge}.png" width="20" height="20"> ' if badge else "🐾 "
        pr_body = f"{body}\n\n---\n{pic}made with purr ({model})"
        cmd = ["gh", "pr", "create", "--title", title, "--body", pr_body, "--head", branch]
        tag = label(root, model)
        if tag:
            cmd += ["--label", tag]
        r = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=120)
        if r.returncode:
            raise RuntimeError(f"pushed {branch}, but gh pr create failed: {(r.stderr or r.stdout).strip()[:300]}")
        return r.stdout.strip().splitlines()[-1]
    finally:
        if back and branch != start:
            git(root, "checkout", start)
