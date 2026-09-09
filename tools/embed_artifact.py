#!/usr/bin/env python3
import argparse
import pathlib


def render(data: bytes, symbol: str) -> str:
    lines = []
    for offset in range(0, len(data), 12):
        chunk = data[offset:offset + 12]
        lines.append("    " + ", ".join(f"0x{byte:02x}" for byte in chunk) + ",")
    body = "\n".join(lines)
    return (
        "#include <stdint.h>\n\n"
        f"const uint8_t {symbol}[] = {{\n{body}\n}};\n"
        f"const uint32_t {symbol}_len = sizeof({symbol});\n"
    )


def main():
    parser = argparse.ArgumentParser(description="Convert a binary artifact to a stable C array.")
    parser.add_argument("input", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    parser.add_argument("symbol")
    args = parser.parse_args()
    if not args.symbol.isidentifier():
        parser.error("symbol must be a valid C identifier")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(args.input.read_bytes(), args.symbol), encoding="ascii")


if __name__ == "__main__":
    main()
