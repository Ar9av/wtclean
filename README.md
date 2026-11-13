# wtclean

Clean up the git worktrees your coding agents leave behind, without losing work.

![wtclean demo](https://raw.githubusercontent.com/Ar9av/wtclean/main/screenshot.png)

## The problem

Claude Code, Codex, Cursor, Conductor and friends run each task in its own **git worktree**: a second checkout of your repo in another folder, on its own branch. That's what lets five agents work on one repo without stepping on each other.

Nobody deletes them afterwards. The agent finishes, the PR merges, and the folder stays, with its own copy of `node_modules`, build output and `.venv`. On one machine after a few weeks:

- **287 worktrees across 101 repos**, two repos with 140+ each
- **54 GB** of disk, mostly duplicated dependencies
- `git worktree list` too long to read, branch pickers full of dead branches
- no way to tell which ones an agent is still using

The obvious fixes don't work:

| You try | What happens |
|---|---|
| `git worktree prune` | Only forgets worktrees whose folder is *already* deleted. Frees nothing. |
| `rm -rf` the folders | Deletes uncommitted work, and any `.env` you copied in. Git's metadata is left dangling. |
| `git worktree remove --force` | Same: `--force` is the flag that skips the "you have changes" check. |
| Ask the agent to clean up | [It happens](https://mergify.com/blog/the-day-my-ai-agent-deleted-29-git-worktrees): an agent removed 29 active worktrees. |

## What wtclean does

It looks at every worktree and sorts it into one of three groups:

- **REMOVE** if it's clean and either
  - its branch is merged or the remote branch was deleted, and it's been idle for 24 hours, or
  - it's unmerged and has been idle for 7 days. The folder is removed and the branch is kept.
- **PRUNE** if the folder is already gone and git still lists it.
- **KEEP** if it has uncommitted changes, is locked, has a process running inside it, was used recently, holds a `.env` the main checkout doesn't have, or is a detached checkout with unpushed commits.

It's a dry run until you add `--apply`.

### Why nothing gets lost

- Removal uses plain `git worktree remove`. Git itself refuses to delete a worktree with changes.
- A branch is only deleted with `git branch -d`, which refuses unmerged work. Removing the folder of an unmerged branch keeps every commit, and `git worktree add <path> <branch>` brings it back.
- What does get deleted is ignored stuff: `node_modules`, build output, caches. That's the disk you get back. Ignored `.env` files are checked first, and a worktree is kept if its `.env` differs from the main checkout's.

## Install

```bash
pipx install git-wtclean      # or: uv tool install git-wtclean / pip install git-wtclean
```

That gives you `wtclean`, and `git wtclean` works too. Needs git and Python 3.9+, macOS or Linux.

No pip? It's one file:

```bash
mkdir -p ~/.local/bin
curl -fsSL https://raw.githubusercontent.com/Ar9av/wtclean/main/git_wtclean.py -o ~/.local/bin/wtclean
chmod +x ~/.local/bin/wtclean
```

If `wtclean` isn't found afterwards, add `export PATH="$HOME/.local/bin:$PATH"` to your `~/.zshrc` or `~/.bashrc`.

## Use

```bash
wtclean                                  # what would go, in the repo you're in
wtclean --scan ~/code                    # same, for every repo under ~/code
wtclean --scan ~/code --fetch --apply    # refresh from remotes, then clean up
```

Want to see it before pointing it at real repos? Clone this repo and run `./demo.sh`. It builds a throwaway repo full of fake agent worktrees and runs wtclean on it.

The first `--apply` on a big backlog can take several minutes, mostly deleting `node_modules`. It shows progress as it goes.

## Options

| Flag | Default | |
|------|---------|--|
| `--scan DIR` | | find repos under DIR (3 levels deep) |
| `--apply` | off | actually remove |
| `--fetch` | off | `git fetch --prune` first, so merged / deleted-on-remote is current |
| `--idle H` | 24 | hours untouched before a merged worktree is removed |
| `--stale D` | 7 | days untouched before an unmerged worktree is removed |
| `--all` | off | also list KEEP rows, with the reason |
| `--json` | off | machine-readable output |

## Use it from your agents

Paste this into `AGENTS.md` or `CLAUDE.md`:

```
Worktrees: lock the worktree you work in with
`git worktree lock --reason "<task>" <path>` and unlock it when you finish.
To check for leftovers, run `wtclean --json`. Only run `wtclean --apply` when the user asks.
```

`--json` returns one object per worktree: `path`, `branch`, `action` (`remove` / `prune` / `keep`), `reason`, `idle_h`.

Or run it on a schedule. This crontab line cleans up every Monday at 9am:

```
0 9 * * 1  ~/.local/bin/wtclean --scan ~/code --fetch --apply >> ~/.wtclean.log 2>&1
```

## Development

```bash
./test.sh   # builds a scratch repo covering every case and checks each verdict
```

One file, standard library only. MIT licensed.
