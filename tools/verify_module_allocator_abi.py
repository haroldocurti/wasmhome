#!/usr/bin/env python3
"""Verify the manager and pinned WAMR runtime use the same allocator ABI."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path


MACRO = "WASM_MEM_ALLOC_WITH_USAGE"
SOURCES = {
    "manager": "main/module_manager.c",
    "runtime": "wasm-micro-runtime/core/iwasm/common/wasm_memory.c",
}


def command_tokens(entry: dict[str, object]) -> list[str]:
    arguments = entry.get("arguments")
    if isinstance(arguments, list) and all(isinstance(item, str) for item in arguments):
        return arguments
    command = entry.get("command")
    if isinstance(command, str):
        return shlex.split(command)
    raise ValueError("compile command has neither a string command nor arguments")


def definitions(tokens: list[str]) -> list[str]:
    found: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "-D":
            index += 1
            if index >= len(tokens):
                raise ValueError("compiler command ends with an incomplete -D option")
            definition = tokens[index]
        elif token.startswith("-D"):
            definition = token[2:]
        else:
            index += 1
            continue
        if definition == MACRO or definition.startswith(MACRO + "="):
            found.append(definition)
        index += 1
    return found


def check_compile_commands(path: Path) -> list[str]:
    entries = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError("compile_commands.json root must be an array")
    report: list[str] = []
    for role, suffix in SOURCES.items():
        matches = [entry for entry in entries if str(entry.get("file", "")).replace("\\", "/").endswith(suffix)]
        if len(matches) != 1:
            raise ValueError(f"expected exactly one {role} translation unit ending in {suffix}; found {len(matches)}")
        flags = definitions(command_tokens(matches[0]))
        if flags != [MACRO + "=1"]:
            raise ValueError(f"{role} must have exactly one -D{MACRO}=1 and no conflicting definition; got {flags}")
        if role == "runtime":
            tokens = command_tokens(matches[0])
            forced_includes = [
                tokens[index + 1]
                for index, token in enumerate(tokens[:-1])
                if token == "-include"
            ]
            forced_includes.extend(
                token[len("-include") :]
                for token in tokens
                if token.startswith("-include") and token != "-include"
            )
            if not any(path.replace("\\", "/").endswith("main/wamr_allocator_abi_guard.h") for path in forced_includes):
                raise ValueError("runtime must force-include main/wamr_allocator_abi_guard.h")
        report.append(f"{role}: -D{flags[0]}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("compile_commands", type=Path)
    args = parser.parse_args()
    try:
        for line in check_compile_commands(args.compile_commands):
            print(line)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"allocator ABI verification failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
