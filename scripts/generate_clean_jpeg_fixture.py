"""Generate the deterministic clean JPEG fixture for Implementation Slice 04."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "testdata/e2e/clean_fixture.png"
OUTPUT = PROJECT / "testdata/e2e/clean_fixture.jpg"
SAVE_OPTIONS = {
    "quality": 95,
    "subsampling": 0,
    "optimize": False,
    "progressive": False,
}


def generate_fixture(source_path: Path, output_path: Path) -> None:
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite fixture: {output_path}")
    with Image.open(source_path) as source:
        source.load()
        if source.size != (1024, 1024):
            raise ValueError("source fixture dimensions changed")
        rgb = source.convert("RGB")
    rgb.save(output_path, format="JPEG", **SAVE_OPTIONS)
    with Image.open(output_path) as generated:
        generated.load()
        if generated.format != "JPEG" or generated.mode != "RGB" or generated.size != (1024, 1024):
            raise ValueError("generated fixture validation failed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    generate_fixture(args.source, args.output)
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
