"""Command line interface."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys

from . import __version__
from .engine import BASELINE_NAME, Config, check, write_baseline
from .replay import ReplayError, render_text, replay
from .reporters import FORMATS
from .rules import all_rules
from .scaffold import HOOK_DISPATCHER, PRE_COMMIT, scaffold

EPILOG = """\
examples:
  agsync check                    lint the current repository
  agsync check --format github    emit inline annotations in CI
  agsync check --baseline         record today's violations and start clean
  agsync replay .                 how many past pushes the gate would have caught
  agsync init                     scaffold a memory structure
  agsync install-hooks            gate commits locally
"""


def _cmd_check(args) -> int:
    root = os.path.abspath(args.path)
    try:
        config = Config.load(root)
    except ValueError as exc:
        print(f"agsync: invalid config: {exc}", file=sys.stderr)
        return 2

    if args.baseline:
        report = check(root, config, use_baseline=False)
        path = write_baseline(root, report.findings)
        print(
            f"Wrote {len(report.findings)} existing violation(s) to "
            f"{os.path.relpath(path, root)}.\n"
            f"These are now ignored; new ones will fail. Delete entries as you fix them."
        )
        return 0

    report = check(root, config, use_baseline=not args.no_baseline)
    FORMATS[args.format](report)
    if args.warn_only:
        return 0
    return 0 if report.ok else 1


def _cmd_replay(args) -> int:
    """Report on history; never gate it.

    Exit 0 even when every commit would have been rejected — a non-zero exit
    here would mean "replay failed", and scripts have to be able to tell those
    apart.
    """
    tty = sys.stderr.isatty()
    emitted = False

    def progress(done: int, total: int, sha: str) -> None:
        nonlocal emitted
        if tty:
            emitted = True
            print(f"\r  {done}/{total}  {sha[:7]}", end="", file=sys.stderr, flush=True)

    def clear() -> None:
        nonlocal emitted
        if emitted:
            print("\r\033[K", end="", file=sys.stderr, flush=True)
            emitted = False

    try:
        result = replay(args.repo, args.ref, progress=progress)
    except ReplayError as exc:
        clear()
        print(f"agsync: {exc}", file=sys.stderr)
        return 1
    clear()

    if args.format == "json":
        json.dump(result.as_dict(), sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        render_text(result, show_first_seen=args.first_seen)
    return 0


def _cmd_init(args) -> int:
    root = os.path.abspath(args.path)
    created = scaffold(root, force=args.force)
    if not created:
        print("Nothing to do — memory structure already exists (use --force to overwrite).")
        return 0
    for path in created:
        print(f"created  {path}")
    print("\nNext: describe your project's objective in memory/goal.md, then run "
          "`agsync check`.")
    return 0


def _cmd_install_hooks(args) -> int:
    root = os.path.abspath(args.path)
    if not os.path.isdir(os.path.join(root, ".git")):
        print("agsync: not a git repository", file=sys.stderr)
        return 2

    hooks_dir = os.path.join(root, ".agsync", "hooks")
    os.makedirs(hooks_dir, exist_ok=True)

    # Preserve whatever hook system is already installed: the dispatcher
    # chains to the previous hooksPath instead of replacing it.
    previous = _git(root, "config", "--get", "core.hooksPath") or ""
    if previous and not previous.startswith(".agsync"):
        chain = previous
    else:
        chain = ".git/hooks"

    for name, template in (("_dispatch", HOOK_DISPATCHER), ("pre-commit", PRE_COMMIT)):
        path = os.path.join(hooks_dir, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(template.replace("@CHAIN@", chain))
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP)
        print(f"created  {os.path.relpath(path, root)}")

    _git(root, "config", "core.hooksPath", ".agsync/hooks")
    print("set      core.hooksPath = .agsync/hooks")
    print(f"chaining to {chain}")
    print("\nUndo with: git config --unset core.hooksPath")
    return 0


def _cmd_rules(args) -> int:
    width = max(len(rule.name) for rule in all_rules())
    for rule in all_rules():
        print(f"{rule.name:<{width}}  {rule.default_severity:<5}  {rule.description}")
    return 0


def _git(root: str, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=False
        )
        return out.stdout.strip()
    except FileNotFoundError:
        return ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agsync",
        description="Lint the memory your AI agents read and write.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"agsync {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    check_parser = subparsers.add_parser("check", help="report integrity violations")
    check_parser.add_argument("path", nargs="?", default=".")
    check_parser.add_argument("--format", choices=sorted(FORMATS), default="text")
    check_parser.add_argument("--warn-only", action="store_true",
                              help="always exit 0 (use on first run)")
    check_parser.add_argument("--baseline", action="store_true",
                              help=f"record current violations into {BASELINE_NAME}")
    check_parser.add_argument("--no-baseline", action="store_true",
                              help="ignore the baseline file and report everything")
    check_parser.set_defaults(fn=_cmd_check)

    replay_parser = subparsers.add_parser(
        "replay", help="replay history and count the pushes that would have been rejected"
    )
    replay_parser.add_argument("repo", help="path or clone URL of the repository to replay")
    replay_parser.add_argument("--ref", default="main",
                               help="branch, remote branch or tag to walk (default: main)")
    replay_parser.add_argument("--first-seen", action="store_true",
                               help="also show when each rule first started failing")
    replay_parser.add_argument("--format", choices=("text", "json"), default="text")
    replay_parser.set_defaults(fn=_cmd_replay)

    init_parser = subparsers.add_parser("init", help="scaffold a memory structure")
    init_parser.add_argument("path", nargs="?", default=".")
    init_parser.add_argument("--force", action="store_true")
    init_parser.set_defaults(fn=_cmd_init)

    hooks_parser = subparsers.add_parser("install-hooks", help="gate commits locally")
    hooks_parser.add_argument("path", nargs="?", default=".")
    hooks_parser.set_defaults(fn=_cmd_install_hooks)

    rules_parser = subparsers.add_parser("rules", help="list every rule")
    rules_parser.set_defaults(fn=_cmd_rules)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        parser.print_help()
        return 0
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
