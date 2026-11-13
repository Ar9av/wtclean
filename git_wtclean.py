#!/usr/bin/env python3
"""wtclean - find and remove dead git worktrees left behind by coding agents.

Dry-run by default. Removal goes through `git worktree remove` (no --force),
so git itself refuses anything dirty. Branches are only deleted with
`git branch -d`, which refuses unmerged work. Nothing is ever rm -rf'd.

  wtclean                      # report for the repo you're in
  wtclean ~/code/a ~/code/b    # several repos
  wtclean --scan ~/code        # every repo under a dir (depth 3)
  wtclean --apply              # actually remove the REMOVE rows
  wtclean --json               # machine-readable, for agents
"""
__version__ = "0.1.0"

import argparse, filecmp, json, os, shutil, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

os.environ["GIT_OPTIONAL_LOCKS"] = "0"  # read-only scans must not rewrite indexes (that resets the idle clock)


def git(cwd, *args):
    r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def worktrees(repo):
    _, out, _ = git(repo, "worktree", "list", "--porcelain")
    wts, cur = [], {}
    for line in out.splitlines() + [""]:
        if not line:
            if cur:
                wts.append(cur)
            cur = {}
            continue
        k, _, v = line.partition(" ")
        cur[k] = v or True
    return wts  # first entry is the main worktree


def default_branch(repo):
    rc, out, _ = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if rc == 0:
        return out
    for b in ("origin/main", "origin/master", "main", "master"):
        if git(repo, "rev-parse", "--verify", "-q", b)[0] == 0:
            return b
    return "HEAD"


def idle_hours(repo, path):
    # last activity = newest of the worktree's index / HEAD in its git admin dir
    _, gitdir, _ = git(path, "rev-parse", "--absolute-git-dir")
    ts = [os.path.getmtime(os.path.join(gitdir, f))
          for f in ("index", "HEAD", "logs/HEAD") if os.path.exists(os.path.join(gitdir, f))]
    return (time.time() - max(ts)) / 3600 if ts else 1e9


def busy_dirs():
    # cwd of every running process: a dev server, shell or agent sitting in a worktree means it's in use
    try:
        r = subprocess.run(["lsof", "-a", "-d", "cwd", "-Fn"], capture_output=True, text=True)
        return {l[1:] for l in r.stdout.splitlines() if l.startswith("n")}
    except FileNotFoundError:  # Linux without lsof
        out = set()
        for pid in os.listdir("/proc") if os.path.isdir("/proc") else []:
            try:
                out.add(os.readlink(f"/proc/{pid}/cwd"))
            except OSError:
                pass
        return out


BUSY = None


def unique_secrets(path, main):
    # ignored .env-style files are deleted with the worktree; flag ones the main checkout doesn't have
    out = []
    for d, subs, files in os.walk(path):
        subs[:] = [s for s in subs if s not in (".git", "node_modules", ".venv", "venv")]
        if d[len(path):].count(os.sep) >= 3:
            subs[:] = []
        for f in files:
            if f.startswith(".env") and not f.endswith((".example", ".sample", ".template")):
                rel = os.path.relpath(os.path.join(d, f), path)
                twin = os.path.join(main, rel)
                if not (os.path.isfile(twin) and filecmp.cmp(twin, os.path.join(d, f), shallow=False)):
                    out.append(rel)
    return out


def classify(repo, wt, base, gone, idle_min, stale_h):
    path, sha = wt["worktree"], wt.get("HEAD", "")
    branch = wt.get("branch", "").removeprefix("refs/heads/") or None
    row = {"repo": repo, "path": path, "branch": branch or f"(detached {sha[:7]})",
           "action": "keep", "reason": ""}
    if wt.get("prunable") or not os.path.isdir(path):
        return {**row, "action": "prune", "reason": "dir missing"}
    if wt.get("locked"):
        return {**row, "reason": "locked"}
    if any(b == path or b.startswith(path + "/") for b in BUSY):
        return {**row, "reason": "process running here"}
    idle = idle_hours(repo, path)  # before status: status may rewrite the index and reset the clock
    row["idle_h"] = round(idle)
    rc, dirty, _ = git(path, "status", "--porcelain")
    if rc or dirty:
        return {**row, "reason": f"uncommitted changes ({plural(len(dirty.splitlines()), 'file')})" if not rc else "status failed"}
    if idle < idle_min:
        return {**row, "reason": f"active {idle:.0f}h ago"}
    secrets = unique_secrets(path, repo)
    if secrets:
        return {**row, "reason": f"has {secrets[0]} not in main checkout"}
    merged = git(repo, "merge-base", "--is-ancestor", sha, base)[0] == 0
    if merged:
        return {**row, "action": "remove", "reason": f"merged into {base}", "del_branch": bool(branch)}
    if branch in gone:
        return {**row, "action": "remove", "reason": "remote branch gone (PR closed/squashed)"}
    if not branch:  # detached: only safe if its commits live on some remote ref
        _, on_remote, _ = git(repo, "for-each-ref", "--contains", sha, "--count=1", "refs/remotes")
        if not on_remote:
            return {**row, "reason": "detached with unpushed commits"}
    # clean, idle, unmerged: removing the worktree is lossless, the branch keeps the commits,
    # but in-flight work gets a longer grace period than merged/abandoned branches
    if idle < stale_h:
        return {**row, "reason": f"unmerged, idle {idle / 24:.0f}d < --stale"}
    return {**row, "action": "remove", "reason": f"idle {idle / 24:.0f}d, branch kept"}


def scan_repo(repo, idle_min, stale_h):
    wts = worktrees(repo)
    if not wts:
        return []
    main = wts[0]["worktree"]
    base = default_branch(main)
    _, refs, _ = git(main, "for-each-ref", "--format=%(refname:short)|%(upstream:track)", "refs/heads")
    gone = {l.split("|")[0] for l in refs.splitlines() if "[gone]" in l}
    with ThreadPoolExecutor(16) as ex:
        return list(ex.map(lambda w: classify(main, w, base, gone, idle_min, stale_h), wts[1:]))


def find_repos(root, depth=3):
    root = os.path.expanduser(root)
    for d, subs, _ in os.walk(root):
        if ".git" in subs and os.path.isdir(os.path.join(d, ".git")):  # skip linked worktrees (.git file)
            yield d
            subs[:] = []
        elif d[len(root):].count(os.sep) >= depth:
            subs[:] = []
        else:
            subs[:] = [s for s in subs if not s.startswith(".") and s != "node_modules"]


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def apply(rows):
    todo = [r for r in rows if r["action"] == "remove"]
    for i, r in enumerate(todo, 1):
        if sys.stderr.isatty():  # big node_modules trees take a while; show we're alive
            line = f"[{i}/{len(todo)}] removing {r['path'].replace(os.path.expanduser('~'), '~')}"
            sys.stderr.write("\r\033[K" + line[:shutil.get_terminal_size().columns - 1])
            sys.stderr.flush()
        rc, _, err = git(r["repo"], "worktree", "remove", r["path"])
        r["result"] = "removed" if rc == 0 else f"failed: {err.splitlines()[-1] if err else rc}"
        if rc == 0 and r.get("del_branch") and git(r["repo"], "branch", "-d", r["branch"])[0] == 0:
            r["result"] += " +branch"
    if todo and sys.stderr.isatty():
        sys.stderr.write("\r\033[K")
    for repo in {r["repo"] for r in rows}:
        git(repo, "worktree", "prune")
    for r in rows:
        if r["action"] == "prune":
            r["result"] = "pruned"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"wtclean {__version__}")
    p.add_argument("repos", nargs="*", help="repo paths (default: current repo)")
    p.add_argument("--scan", metavar="DIR", help="find repos under DIR")
    p.add_argument("--idle", type=float, default=24, help="hours untouched before a worktree counts as dead (default 24)")
    p.add_argument("--stale", type=float, default=7, help="days idle before an UNMERGED worktree counts as dead (default 7)")
    p.add_argument("--apply", action="store_true", help="remove the REMOVE rows (default: dry run)")
    p.add_argument("--fetch", action="store_true", help="git fetch --prune first, so merged/gone is current")
    p.add_argument("--json", action="store_true", help="print JSON (for agents)")
    p.add_argument("--all", action="store_true", help="also list KEEP rows")
    a = p.parse_args()
    global BUSY
    BUSY = busy_dirs()

    repos = list(a.repos) or ([] if a.scan else ["."])
    if a.scan:
        repos += list(find_repos(a.scan))
    found = set()
    for r in repos:
        rc, d, _ = git(os.path.expanduser(r), "rev-parse", "--path-format=absolute", "--git-common-dir")
        if rc:
            sys.exit(f"wtclean: {r} is not a git repo. Run it inside a repo, or use --scan DIR.")
        found.add(d.removesuffix("/.git"))
    repos = found
    if a.fetch:
        with ThreadPoolExecutor(8) as ex:
            list(ex.map(lambda r: git(r, "fetch", "--prune", "-q"), repos))
    rows = [row for r in sorted(repos) for row in scan_repo(r, a.idle, a.stale * 24)]
    if a.apply:
        apply(rows)

    if a.json:
        json.dump(rows, sys.stdout, indent=1)
        print()
        return
    home = os.path.expanduser("~")
    color = sys.stdout.isatty()
    paint = {"remove": "\033[31m", "prune": "\033[33m", "keep": "\033[32m"}
    shown = [r for r in rows if r["action"] != "keep" or a.all]
    pw = max([len(r["path"].replace(home, "~")) for r in shown] + [10])
    bw = max([len(r["branch"][:38]) for r in shown] + [6])
    for r in shown:
        tag = r["action"].upper().ljust(6)
        if color:
            tag = paint[r["action"]] + tag + "\033[0m"
        print(f"{tag} {r['path'].replace(home, '~'):<{pw}}  {r['branch'][:38]:<{bw}}   {r['reason']}"
              + (f"  -> {r['result']}" if "result" in r else ""))
    n = {k: sum(r["action"] == k for r in rows) for k in ("remove", "prune", "keep")}
    verb = "removed" if a.apply else "would remove"
    print(f"\n{plural(len(rows), 'worktree')} in {plural(len(repos), 'repo')}: {verb} {n['remove']}, "
          f"prune {n['prune']}, keep {n['keep']}" + ("" if a.apply else "  (dry run: add --apply to do it)"))
    if any(r.get("result", "").startswith("failed") for r in rows):
        sys.exit(1)


if __name__ == "__main__":
    main()
