#!/usr/bin/env python3
import argparse
import pathlib
import re

C_KEYWORDS = set("auto break case char const continue default do double else enum extern float for goto if inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while _Alignas _Alignof _Atomic _Bool _Complex _Generic _Imaginary _Noreturn _Static_assert _Thread_local".split())

def render(data: bytes, symbol: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", symbol) or symbol in C_KEYWORDS:
        raise ValueError("symbol must be an ASCII C identifier")
    if not data:
        raise ValueError("artifact must not be empty")
    lines = []
    for offset in range(0, len(data), 12):
        chunk = data[offset:offset + 12]
        lines.append("    " + ", ".join(f"0x{byte:02x}" for byte in chunk) + ",")
    body = "\n".join(lines)
    return (
        "#include <stdint.h>\n\n"
        f"_Alignas(8) const uint8_t {symbol}[] = {{\n{body}\n}};\n"
        f"const uint32_t {symbol}_len = sizeof({symbol});\n"
    )


def main():
    parser = argparse.ArgumentParser(description="Convert a binary artifact to a stable C array.")
    parser.add_argument("input", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    parser.add_argument("symbol")
    args = parser.parse_args()
    try:
        source = render(args.input.read_bytes(), args.symbol)
    except ValueError as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(source, encoding="ascii")


if __name__ == "__main__":
    main()
