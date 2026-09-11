"""Prepare the privacy-screened real-photo JPEG corpus for Slice 04B."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil

from PIL import Image, ImageCms


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT / "testdata/jpeg_realworld"
SENSITIVE_EXIF_TAGS = {
    306,    # DateTime
    315,    # Artist
    33432,  # Copyright
    34853,  # GPSInfo
    36867,  # DateTimeOriginal
    36868,  # DateTimeDigitized
    37500,  # MakerNote
    37510,  # UserComment
    42033,  # BodySerialNumber
    42037,  # LensSerialNumber
}
SOURCE_MAP = {
    "camera_orientation_01.jpg": "source_01.jpg",
    "camera_orientation_02.jpg": "source_04_landscape.jpg",
    "camera_orientation_03.jpg": "source_02.jpg",
    "camera_orientation_06_icc.jpg": "source_03.jpg",
    "camera_orientation_08.jpg": "source_05_landscape.jpg",
}


def _validate_source(path: Path, expected_orientation: int) -> None:
    with Image.open(path) as image:
        image.load()
        if image.format != "JPEG" or image.mode != "RGB":
            raise ValueError(f"source is not an RGB JPEG: {path}")
        exif = image.getexif()
        if exif.get(274) != expected_orientation:
            raise ValueError(f"unexpected orientation for {path.name}")
        sensitive = SENSITIVE_EXIF_TAGS & set(exif.keys())
        if sensitive:
            raise ValueError(f"privacy-sensitive EXIF tag IDs present in {path.name}: {sorted(sensitive)}")
        if image.info.get("xmp") or image.info.get("comment"):
            raise ValueError(f"XMP/comment requires separate privacy review: {path.name}")


def _insert_icc_app2(source: Path, target: Path, profile: bytes) -> None:
    raw = source.read_bytes()
    if raw[:2] != b"\xff\xd8":
        raise ValueError("invalid JPEG signature")
    payload = b"ICC_PROFILE\x00\x01\x01" + profile
    if len(payload) + 2 > 65535:
        raise ValueError("ICC profile exceeds one JPEG APP2 segment")
    marker = b"\xff\xe2" + (len(payload) + 2).to_bytes(2, "big") + payload
    insertion = 2
    offset = 2
    while raw[offset : offset + 2] == b"\xff\xe0":
        segment_length = int.from_bytes(raw[offset + 2 : offset + 4], "big")
        insertion = offset + 2 + segment_length
        offset = insertion
    target.write_bytes(raw[:insertion] + marker + raw[insertion:])


def prepare(source_dir: Path, output_dir: Path) -> None:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite corpus directory: {output_dir}")
    expected = {int(name.split("_")[2][:2]) for name in SOURCE_MAP}
    for target_name, source_name in SOURCE_MAP.items():
        orientation = int(target_name.split("_")[2][:2])
        _validate_source(source_dir / source_name, orientation)
    if expected != {1, 2, 3, 6, 8}:
        raise ValueError("orientation corpus definition changed")

    output_dir.mkdir(parents=True)
    for target_name, source_name in SOURCE_MAP.items():
        source = source_dir / source_name
        target = output_dir / target_name
        if target_name.endswith("_icc.jpg"):
            profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
            _insert_icc_app2(source, target, profile)
        else:
            shutil.copyfile(source, target)

    for target_name in SOURCE_MAP:
        orientation = int(target_name.split("_")[2][:2])
        _validate_source(output_dir / target_name, orientation)
    with Image.open(output_dir / "camera_orientation_06_icc.jpg") as image:
        if not image.info.get("icc_profile"):
            raise ValueError("ICC fixture profile insertion failed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    prepare(args.source_dir, args.output_dir)
    print(args.output_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
