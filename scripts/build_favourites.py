#!/usr/bin/env python3
"""Infer artist affinity from a playlist export, or build it from a manual list.

Playlist text is expected to contain one track per line in the form:

    歌名 - 歌手
    歌名 - 歌手 A / 歌手 B

Each unique track contributes one count to every listed artist. The output is
artist-ranks.json and contains no need for album-level ranking.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from common import CliError, configure_stdio, emit_cli_error, normalize_key, write_json

ARTIST_SPLIT = re.compile(r"\s*、\s*|\s+/\s+")
MANUAL_SPLIT = re.compile(r"\s*[,，;；]\s*")


def split_track(line: str):
    if " - " not in line:
        return None
    title, artist_text = line.rsplit(" - ", 1)
    title = title.strip()
    artists = [part.strip() for part in ARTIST_SPLIT.split(artist_text) if part.strip()]
    if not title or not artists:
        return None
    return title, artists


def build_from_playlist(path: str) -> dict:
    playlist = Path(path).expanduser().resolve()
    if not playlist.is_file():
        raise CliError(f"歌单文件不存在：{playlist}", 4)
    try:
        lines = playlist.read_text(encoding="utf-8-sig").splitlines()
    except UnicodeDecodeError as exc:
        raise CliError(f"歌单文件不是 UTF-8 编码：{playlist}", 4) from exc
    except OSError as exc:
        raise CliError(f"无法读取歌单：{playlist}：{exc}", 4) from exc

    seen = set()
    counts = Counter()
    display_names = {}
    malformed = []
    duplicate_lines = 0
    for line_number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue
        parsed = split_track(stripped)
        if parsed is None:
            malformed.append(line_number)
            continue
        title, artists = parsed
        unique_artists = []
        for artist in artists:
            key = normalize_key(artist)
            if key and key not in {normalize_key(item) for item in unique_artists}:
                unique_artists.append(artist)
        signature = (normalize_key(title), tuple(sorted(normalize_key(item) for item in unique_artists)))
        if signature in seen:
            duplicate_lines += 1
            continue
        seen.add(signature)
        for artist in unique_artists:
            key = normalize_key(artist)
            display_names.setdefault(key, artist)
            counts[key] += 1

    if not counts:
        raise CliError("没有从歌单中解析出任何歌手；请检查每行是否为“歌名 - 歌手”", 4)
    return {
        "source_type": "playlist",
        "playlist": str(playlist),
        "unique_tracks": len(seen),
        "duplicate_lines": duplicate_lines,
        "malformed_lines": malformed,
        "counts": counts,
        "display_names": display_names,
    }


def build_from_manual(text: str) -> dict:
    artists = [part.strip() for part in MANUAL_SPLIT.split(text) if part.strip()]
    if not artists:
        raise CliError("--artists 没有提供有效歌手名", 2)
    counts = Counter()
    display_names = {}
    manual_order = []
    for artist in artists:
        key = normalize_key(artist)
        if key in counts:
            continue
        display_names[key] = artist
        counts[key] = 1
        manual_order.append(key)
    return {
        "source_type": "manual",
        "playlist": None,
        "unique_tracks": None,
        "duplicate_lines": 0,
        "malformed_lines": [],
        "counts": counts,
        "display_names": display_names,
        "manual_order": manual_order,
    }


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="从歌单或手动名单生成喜爱歌手排名")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--playlist", help="UTF-8 文本歌单；每行：歌名 - 歌手")
    group.add_argument("--artists", help="手动歌手列表，逗号分隔；越靠前越喜爱")
    parser.add_argument("--out", default="artist-ranks.json")
    args = parser.parse_args()

    try:
        built = build_from_playlist(args.playlist) if args.playlist else build_from_manual(args.artists)
        counts = built.pop("counts")
        display_names = built.pop("display_names")
        manual_order = built.pop("manual_order", None)
        ordered = manual_order if manual_order is not None else sorted(
            counts, key=lambda key: (-counts[key], display_names[key].casefold())
        )
        artists = {}
        for ordinal, key in enumerate(ordered, 1):
            name = display_names[key]
            songs = int(counts[key])
            share = round(songs / built["unique_tracks"], 6) if built["unique_tracks"] else None
            artists[name] = {"rank": ordinal, "songs": songs, "share": share}
        output = {
            "schema_version": 1,
            **built,
            "artist_count": len(artists),
            "artists": artists,
        }
        destination = write_json(args.out, output, indent=1)
        summary = {
            "ok": True,
            "source_type": output["source_type"],
            "artists": len(artists),
            "unique_tracks": output["unique_tracks"],
            "duplicate_lines": output["duplicate_lines"],
            "malformed_lines": len(output["malformed_lines"]),
            "out": str(destination),
        }
        print(json.dumps(summary, ensure_ascii=False))
        if output["malformed_lines"]:
            print(
                f"警告：{len(output['malformed_lines'])} 行不符合“歌名 - 歌手”，已跳过。",
                file=sys.stderr,
            )
        return 0
    except CliError as exc:
        return emit_cli_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())