#!/usr/bin/env python3
"""Build an index of local album-cover colours and optional favourite metadata.

The script only reads image files under --src. It never uploads data.
If --static is provided, keys may be album titles, source stems, or a unique
suffix ending in "_<album title>".
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from common import CliError, configure_stdio, emit_cli_error, normalize_key, read_json, write_json

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
NUMBER_PREFIX = re.compile(r"^\s*\d{1,8}\s*(?:[_\-.·\s]+)\s*(.+)$")

BANDS = {
    "red": (15, 45), "yellow": (85, 115), "green": (115, 165), "blue": (255, 300),
    "purple": (285, 315), "orange": (52, 72), "cyan": (188, 212), "magenta": (335, 5),
}
OPP = {
    "red": (150, 250), "yellow": (240, 330), "green": (300, 360), "blue": (60, 110),
    "purple": (60, 160), "orange": (200, 280), "cyan": (0, 60), "magenta": (100, 200),
}


def rgb_to_lab(arr):
    v = arr / 255.0
    lin = np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = (lin @ m.T) / np.array([0.95047, 1.0, 1.08883])
    d = 6 / 29
    f = np.where(xyz > d ** 3, np.cbrt(xyz), xyz / (3 * d ** 2) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])], -1)


def hsv_parts(arr):
    mx, mn = arr.max(-1), arr.min(-1)
    d = np.maximum(mx - mn, 1e-6)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    h = np.where(r == mx, (g - b) / d, np.where(g == mx, 2 + (b - r) / d, 4 + (r - g) / d))
    h = (h / 6.0) % 1.0 * 360
    s = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    return h, s, mx


def in_band(h, lo, hi):
    return ((h >= lo) | (h <= hi)) if lo > hi else ((h >= lo) & (h <= hi))


def parse_cover_name(stem: str, filename_regex: str | None) -> dict:
    """Return a best-effort artist/album split for NNNN_artist_album names."""
    if filename_regex:
        match = re.search(filename_regex, stem, flags=re.IGNORECASE)
        if not match:
            raise CliError(f"文件名不匹配 --filename-regex：{stem}", 4)
        groups = match.groupdict()
        if "album" not in groups:
            raise CliError("--filename-regex 必须包含命名组 album（可选 artist）", 2)
        artist = (groups.get("artist") or "").strip()
        album = (groups.get("album") or stem).strip()
        return {"artist": artist, "album": album or stem}

    payload = NUMBER_PREFIX.sub(r"\1", stem, count=1)
    if "_" in payload:
        artist, album = payload.split("_", 1)
    else:
        artist, album = "", payload
    return {"artist": artist.strip(), "album": (album.strip() or stem)}


def analyse(path: Path) -> dict:
    with Image.open(path) as source:
        im = ImageOps.exif_transpose(source).convert("RGB")
        im = ImageOps.fit(im, (96, 96), method=Image.Resampling.LANCZOS)

    q = im.quantize(colors=7, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()
    cols = sorted(q.getcolors(maxcolors=256) or [], reverse=True)
    if not pal or not cols:
        raise ValueError("无法提取调色板")

    weights = np.array([count for count, _ in cols], dtype=float)
    weights /= weights.sum()
    rgbs = np.array([pal[index * 3:index * 3 + 3] for _, index in cols], dtype=float)
    labs = rgb_to_lab(rgbs)
    lightness, a, b = (labs * weights[:, None]).sum(0)
    chroma = math.hypot(a, b)
    hue = math.degrees(math.atan2(b, a)) % 360
    centre = labs.mean(0)
    dispersion = float(np.sqrt(((labs - centre) ** 2).sum(1) @ weights))

    arr = np.asarray(im, dtype=float) / 255.0
    hh, ss, vv = hsv_parts(arr)
    over = float(((vv > 0.85) & (ss < 0.12)).mean())
    out = {
        "avg_lab": [round(float(x), 2) for x in (lightness, a, b)],
        "avg_LCh": [round(float(lightness), 2), round(float(chroma), 2), round(float(hue), 1)],
        "disp": round(dispersion, 2),
        "overexposed": round(over, 4),
        "clean": {},
    }
    for kind, (lo, hi) in BANDS.items():
        mask = in_band(hh, lo, hi) & (ss >= 0.15) & (vv > 0.06) & (vv < 0.97)
        coverage = float(mask.mean())
        opposite_lo, opposite_hi = OPP.get(kind, (0, 0))
        opposite = float((in_band(hh, opposite_lo, opposite_hi) & (ss >= 0.20) & (vv > 0.08)).mean())
        out["clean"][kind] = {
            "cov": round(coverage, 4),
            "opposite": round(opposite, 4),
            "clean": round(coverage - 0.9 * opposite - 0.7 * over, 4),
        }
    return out


def list_images(source: Path, recursive: bool) -> list[Path]:
    iterator = source.rglob("*") if recursive else source.iterdir()
    files = [
        path for path in iterator
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS and not path.name.startswith(".")
    ]
    return sorted(files, key=lambda p: str(p).casefold())


def locate_metadata(row: dict, static: dict, static_normalized: dict) -> tuple[dict | None, str | None]:
    source_stem = Path(row["source"]).stem
    for key in (source_stem, row.get("album", "")):
        if key in static:
            return static[key], key
    for key in (source_stem, row.get("album", "")):
        hit = static_normalized.get(normalize_key(key))
        if hit is not None:
            return hit[1], hit[0]
    source_key = normalize_key(source_stem)
    for position, char in enumerate(source_key):
        if char != "_":
            continue
        hit = static_normalized.get(source_key[position + 1:])
        if hit is not None:
            return hit[1], hit[0]
    return None, None


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="建立专辑封面颜色索引")
    parser.add_argument("--src", required=True, help="封面目录；只读取此目录")
    parser.add_argument("--out", default="index.json")
    parser.add_argument("--static", help="可选 JSON：{专辑名或源文件名: {artist, rank, songs}}")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--recursive", action="store_true", help="递归扫描子目录")
    parser.add_argument("--filename-regex", help="可选命名组正则，例如 (?P<artist>.*?)_(?P<album>.+)")
    args = parser.parse_args()

    try:
        source = Path(args.src).expanduser().resolve()
        if not source.is_dir():
            raise CliError(f"封面目录不存在或不是目录：{source}", 4)
        if args.workers < 1 or args.workers > 64:
            raise CliError("--workers 必须在 1 到 64 之间", 2)
        if args.filename_regex:
            try:
                re.compile(args.filename_regex)
            except re.error as exc:
                raise CliError(f"--filename-regex 无效：{exc}", 2) from exc

        static = read_json(args.static, "静态元数据") if args.static else {}
        if args.static and not isinstance(static, dict):
            raise CliError("--static 必须是 JSON 对象：{专辑名: {artist, rank, songs}}", 4)

        files = list_images(source, args.recursive)
        if not files:
            raise CliError(f"目录中没有可索引图片：{source}", 4)

        def safe_analyse(path: Path):
            try:
                return path, analyse(path), None
            except Exception as exc:  # one bad image must not abort the whole library
                return path, None, f"{type(exc).__name__}: {exc}"

        rows, errors = [], []
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            for path, row, error in executor.map(safe_analyse, files):
                if error:
                    errors.append({"path": str(path), "error": error})
                    continue
                stem = path.stem
                parsed = parse_cover_name(stem, args.filename_regex)
                rel = os.path.relpath(path, source).replace(os.sep, "/")
                row.update({
                    "id": rel,
                    "source": path.name,
                    "source_stem": stem,
                    "path": str(path),
                    "artist": parsed["artist"],
                    "album": parsed["album"],
                    "rank": None,
                    "songs": None,
                })
                rows.append(row)

        static_normalized = {
            normalize_key(key): (key, value)
            for key, value in static.items()
            if normalize_key(key)
        }
        metadata_matches = 0
        for row in rows:
            meta, matched_key = locate_metadata(row, static, static_normalized)
            if meta is None:
                continue
            if not isinstance(meta, dict):
                raise CliError(f"静态元数据条目必须是对象：{matched_key}", 4)
            metadata_matches += 1
            row["artist"] = str(meta.get("artist", row.get("artist") or "")).strip()
            row["album"] = str(meta.get("album", matched_key or row.get("album") or row["source_stem"])).strip()
            row["rank"] = meta.get("rank") if meta.get("rank") is not None else None
            row["songs"] = meta.get("songs") if meta.get("songs") is not None else None

        for row in rows:
            if not row.get("album"):
                row["album"] = row["source_stem"]
        rows.sort(key=lambda row: row["id"].casefold())

        if args.static and static and metadata_matches == 0:
            raise CliError(
                "提供了 --static，但没有任何条目匹配当前文件名。"
                "请检查 JSON 键，或先不传 --static 只建立颜色索引。",
                4,
            )

        if not rows:
            raise CliError("所有图片都索引失败，未生成可用索引", 4)

        output = write_json(args.out, rows, indent=1)
        summary = {
            "ok": bool(rows),
            "indexed": len(rows),
            "failed": len(errors),
            "metadata_matches": metadata_matches,
            "out": str(output),
        }
        print(json.dumps(summary, ensure_ascii=False))
        if errors:
            print(f"警告：{len(errors)} 张图片无法索引，已跳过。", file=sys.stderr)
            for item in errors[:20]:
                print(f"  {item['path']}: {item['error']}", file=sys.stderr)
            if len(errors) > 20:
                print(f"  ……其余 {len(errors) - 20} 条省略", file=sys.stderr)
        if args.static and metadata_matches < len(rows):
            print(
                f"警告：静态元数据只匹配 {metadata_matches}/{len(rows)} 张；"
                "未匹配项不会参与喜爱模式排序。",
                file=sys.stderr,
            )
        return 0
    except CliError as exc:
        return emit_cli_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())