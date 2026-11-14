"""Shared command-line options for the research scripts.

Every research script works on one workspace, laid out like this:

    research/          --research DIR   (default: ./research)
      additions.json     the append-only evidence ledger
      disputes.json      parent links a record has disproved
      verified/          each skeptic lane's raw verdict, per round
      roundN/            targets.json, roster.json, pages/, gated/
    view/              --view DIR       (default: ./view)
      tree-data.json, tree-data-details.b64   built by build_tree_view.py

Paths are relative to the current directory unless given absolute.
"""
import os


def pop_opt(argv, flag, default=None):
    """Remove `flag VALUE` from argv and return VALUE (or default)."""
    if flag in argv:
        k = argv.index(flag)
        if k + 1 >= len(argv):
            raise SystemExit(f"{flag} needs a value")
        val = argv[k + 1]
        del argv[k:k + 2]
        return val
    return default


def research_dir(argv):
    return os.path.abspath(pop_opt(argv, "--research", "research"))


def view_dir(argv):
    return os.path.abspath(pop_opt(argv, "--view", "view"))
