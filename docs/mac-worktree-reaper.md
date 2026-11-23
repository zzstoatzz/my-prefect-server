# mac worktree reaper

Coding agents create git worktrees and rarely remove them. On 2026-11-23 the
Mac held 464 of them under `~/.codex/worktrees`, `~/Documents/Codex`,
`~/tangled.org/_wt`, `.claude/worktrees` and sibling `<repo>-<topic>`
directories, along with Xcode DerivedData for workspaces that no longer
existed. Together they set off the Mac disk-pressure alert.

`mps.worktree_reaper` runs daily at 04:30 under launchd. It searches
`~/tangled.org`, `~/github.com`, `~/.codex/worktrees` and `~/.cache/checkouts`
for repositories, fetches `origin`, and removes a linked
worktree only when it is clean, untouched for three days, has no process
inside it, and its commits are already upstream: HEAD is on a remote-tracking
ref, every commit has a patch-equivalent on the default branch, or merging it
into the default branch changes nothing (a squash merge). Its branch is
deleted only when the default branch contains the tip. Ignored files that are
not build output, such as `.env` or scratch data, keep the worktree. Every
other worktree is listed in the report with a reason.

```bash
uv run python -m mps.worktree_reaper            # dry run: report only
uv run python -m mps.worktree_reaper --apply
```

macOS blocks a launchd job from listing `~/Documents`, `~/Desktop` and
`~/Downloads` without Full Disk Access, and Python hangs on the prompt. The
job therefore keeps worktrees in those folders and never opens them. Run it by
hand with `--root ~/Documents/Codex` to include Codex's worktrees there.

The report is written to
`~/Library/Application Support/mac-worktree-reaper/last.json`.

## install

```bash
sed -e "s|REPOSITORY_PATH|$PWD|" \
    -e "s|EXECUTABLE_PATH|$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin|" \
    -e "s|LOG_PATH|$HOME/Library/Application Support/mac-worktree-reaper/launchd.log|" \
    deploy/mac-worktree-reaper.plist > ~/Library/LaunchAgents/io.zzstoatzz.mac-worktree-reaper.plist
mkdir -p ~/Library/Application\ Support/mac-worktree-reaper
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/io.zzstoatzz.mac-worktree-reaper.plist
```

## for agents

A worktree you create is yours to remove. When its work lands, or when you
abandon it, run `git worktree remove <path>` from the main checkout. The reaper
is a backstop, not the plan.
