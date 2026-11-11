#!/usr/bin/env bash
# Builds a throwaway repo with one worktree per case, runs wtclean --apply, asserts outcomes.
set -euo pipefail
WT="$(cd "$(dirname "$0")" && pwd)/wtclean"
T=$(cd "$(mktemp -d)" && pwd -P); trap 'kill $SLEEPER 2>/dev/null; rm -rf "$T"' EXIT
cd "$T"; export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
git init -q --bare origin.git; git clone -q origin.git main 2>/dev/null; cd main
echo .env > .gitignore; git add .gitignore; git commit -q -m init; git push -q origin HEAD:main; git remote set-head origin main
c() { git -C "$1" commit -q --allow-empty -m "$2"; }

git worktree add -q ../merged -b merged;    c ../merged m; git merge -q --ff-only merged; git push -q origin HEAD:main
git worktree add -q ../dirty -b dirty;      echo x > ../dirty/f
git worktree add -q ../unmerged -b unmerged; c ../unmerged u
git worktree add -q ../gone-dir -b gone-dir; rm -rf ../gone-dir
git worktree add -q --detach ../detached;   c ../detached d
git worktree add -q ../locked -b locked;    git worktree lock ../locked
git worktree add -q ../fresh -b fresh                                  # touched just now
git worktree add -q ../secret -b secret;   echo k > ../secret/.env     # ignored, not in main
git worktree add -q ../busy -b busy;       (cd ../busy && exec sleep 60) & SLEEPER=$!; sleep 0.3

out=$("$WT" --json --idle 0 --stale 0 . | python3 -c '
import json,sys; print(" ".join(r["path"].rsplit("/",1)[1] + "=" + r["action"] for r in json.load(sys.stdin)))')
echo "$out"
for want in secret=keep busy=keep merged=kill dirty=keep unmerged=kill gone-dir=prune detached=keep locked=keep; do
  [[ " $out " == *" $want "* ]] || { echo "FAIL: expected $want"; exit 1; }
done
"$WT" --idle 24 --json . | grep -q '"active' || { echo "FAIL: fresh worktree should be active"; exit 1; }

"$WT" --apply --idle 0 --stale 0 . >/dev/null
[ ! -d ../merged ] && [ ! -d ../unmerged ] && [ -f ../dirty/f ] && [ -d ../detached ] && [ -d ../locked ] && [ -f ../secret/.env ] && [ -d ../busy ] || { echo "FAIL: apply"; exit 1; }
git rev-parse -q --verify refs/heads/merged >/dev/null && { echo "FAIL: merged branch should be deleted"; exit 1; }
git rev-parse -q --verify refs/heads/unmerged >/dev/null || { echo "FAIL: unmerged branch must survive"; exit 1; }
git worktree list | grep -q gone-dir && { echo "FAIL: gone-dir not pruned"; exit 1; }
echo PASS
