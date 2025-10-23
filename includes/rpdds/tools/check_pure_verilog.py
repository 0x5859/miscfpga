#!/usr/bin/env python3
"""Simple RTL hygiene checker for the pure Verilog scaffold.

This script is intentionally lightweight so it can run inside a minimal Python
environment without external HDL parsers. It does not replace a real compiler,
but it is useful for catching accidental regressions such as SystemVerilog-only
keywords or unbalanced block delimiters.
"""

from __future__ import annotations

import re
from pathlib import Path

RTL_DIR = Path(__file__).resolve().parents[1] / "rtl"
FORBIDDEN = [
    r"\blogic\b",
    r"\balways_ff\b",
    r"\balways_comb\b",
    r"\btypedef\b",
    r"\benum\b",
    r"\bunique\b",
    r"\binterface\b",
    r"\bmodport\b",
]


def strip_comments(text: str) -> str:
    text = re.sub(r"//.*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return text


def check_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    clean = strip_comments(text)
    errors: list[str] = []

    for pattern in FORBIDDEN:
        if re.search(pattern, clean):
            errors.append(f"forbidden SystemVerilog token matched: {pattern}")

    tokens = re.findall(r"`\w+|\b\w+\b|\S", clean)
    pairs = [
        ("module", "endmodule"),
        ("begin", "end"),
        ("case", "endcase"),
        ("function", "endfunction"),
    ]
    for start, stop in pairs:
        if tokens.count(start) != tokens.count(stop):
            errors.append(
                f"unbalanced tokens: {start}={tokens.count(start)} {stop}={tokens.count(stop)}"
            )

    return errors


def main() -> int:
    failed = False
    for path in sorted(RTL_DIR.glob("*.v")):
        errors = check_file(path)
        if errors:
            failed = True
            print(f"[FAIL] {path.name}")
            for err in errors:
                print(f"  - {err}")
        else:
            print(f"[PASS] {path.name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
