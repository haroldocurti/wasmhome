#!/usr/bin/env python3
"""Bind a #22 build to an unchanged, clean Git revision and tree."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess


def source_identity(root: Path, *, expected_commit: str | None = None,
                    expected_tree: str | None = None) -> dict[str, str]:
    root = root.resolve()

    def git(*arguments: str) -> str:
        return subprocess.check_output(["git", "-C", str(root), *arguments],
                                       text=True, stderr=subprocess.PIPE).strip()

    if Path(git("rev-parse", "--show-toplevel")).resolve() != root:
        raise ValueError("#22 source must be the Git repository root")
    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("#22 build source must be clean, including untracked files")
    identity = {"source_commit": git("rev-parse", "HEAD"),
                "source_tree": git("rev-parse", "HEAD^{tree}")}
    if any(not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value)
           for value in identity.values()):
        raise ValueError("#22 Git source identity is malformed")
    for key, expected in (("source_commit", expected_commit), ("source_tree", expected_tree)):
        if expected is not None and identity[key] != expected:
            raise ValueError("#22 source changed since configuration; start a fresh build")
    return identity


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-tree")
    parser.add_argument("--cmake", action="store_true")
    options = parser.parse_args()
    try:
        identity = source_identity(options.root, expected_commit=options.expected_commit,
                                   expected_tree=options.expected_tree)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"#22 source identity rejected: {error}\n")
    print(";".join(identity.values()) if options.cmake else json.dumps(identity, sort_keys=True))


if __name__ == "__main__":
    main()
