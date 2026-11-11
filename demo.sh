#!/usr/bin/env bash
# 20-second demo: builds a fake repo littered with agent worktrees, then runs wtclean on it.
#   ./demo.sh          dry run
#   ./demo.sh --apply  dry run, then clean up
set -euo pipefail
WT="$(cd "$(dirname "$0")" && pwd)/wtclean"
T=$(cd "$(mktemp -d)" && pwd -P); trap 'rm -rf "$T"' EXIT
export GIT_AUTHOR_NAME=demo GIT_AUTHOR_EMAIL=demo@example.com GIT_COMMITTER_NAME=demo GIT_COMMITTER_EMAIL=demo@example.com
cd "$T"; git init -q --bare origin.git; git clone -q origin.git app 2>/dev/null; cd app
echo .env > .gitignore; git add .gitignore; git commit -q -m init; git push -q origin HEAD:main; git remote set-head origin main

age() {  # pretend the worktree was last touched N days ago
  local g; g=$(git -C "$1" rev-parse --absolute-git-dir)
  touch -t "$(date -v-"$2"d +%Y%m%d%H%M 2>/dev/null || date -d "-$2 days" +%Y%m%d%H%M)" "$g/index" "$g/HEAD" "$g/logs/HEAD" 2>/dev/null || true
}
wt() { git worktree add -q "../$1" -b "$2"; git -C "../$1" commit -q --allow-empty -m "$2"; }

wt claude-fix-login     claude/fix-login;     git merge -q --ff-only claude/fix-login; git push -q origin HEAD:main; age ../claude-fix-login 9
wt codex-add-tests      codex/add-tests;      age ../codex-add-tests 21
wt cursor-refactor-api  cursor/refactor-api;  age ../cursor-refactor-api 12
wt claude-dark-mode     claude/dark-mode;     echo wip > ../claude-dark-mode/theme.css; age ../claude-dark-mode 30
wt codex-billing        codex/billing;        echo KEY=1 > ../codex-billing/.env; age ../codex-billing 15
wt claude-search        claude/search;        age ../claude-search 2
wt codex-migrate-db     codex/migrate-db                                       # agent working right now
wt conductor-onboarding conductor/onboarding; rm -rf ../conductor-onboarding   # deleted by hand

echo "\$ git worktree list | wc -l"; git worktree list | wc -l
echo; echo "\$ wtclean --all"; HOME="$T" "$WT" --all .
if [[ "${1:-}" == --apply ]]; then echo; echo "\$ wtclean --apply"; HOME="$T" "$WT" --apply .; fi
