#!/usr/bin/env python3
"""A stand-in for `nvidia-smi --query-gpu=... --format=csv,noheader,nounits -l 1`, for tests and screenshots.

It prints two made-up graphics cards once a second (once only without `-l`). Set FAKE_GPUS=1 for one card.
Columns: index, name, load %, memory used MiB, memory total MiB, temperature, power W.
"""
import os
import sys
import time

CARDS = [("Demo Card A 24GB", 24576, 21900, 250.0), ("Demo Card B 12GB", 12288, 6400, 70.0)]


def rows(tick: int) -> list[str]:
    out = []
    for i, (name, total, used, power) in enumerate(CARDS[:int(os.environ.get("FAKE_GPUS", "2"))]):
        wobble = (tick * 7 + i * 3) % 5
        out.append(f"{i}, {name}, {60 + wobble * 4 - i * 35}, {used + wobble * 8}, {total}, {58 + wobble - i * 9}, {power + wobble * 3:.2f}")
    return out


def main() -> int:
    loop = "-l" in sys.argv
    tick = 0
    while True:
        try:
            print("\n".join(rows(tick)), flush=True)
        except (BrokenPipeError, OSError):
            return 0
        if not loop:
            return 0
        tick += 1
        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
