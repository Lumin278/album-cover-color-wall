#!/usr/bin/env python3
"""End-to-end self-test with synthetic covers only. No personal data required."""
from __future__ import annotations

import argparse
import colorsys
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from common import configure_stdio
from make_wall import select_pool

HERE = Path(__file__).resolve().parent
PY = sys.executable
ENV = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")


def make_fixture(directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    metadata = {}

    def add_meta(title, artist, rank):
        metadata[title] = {"artist": artist, "rank": rank, "songs": max(1, 20 - rank)}

    def save(name, image, title, artist, rank):
        path = directory / f"{name}_{artist}_{title}.jpg"
        image.save(path, quality=92)
        add_meta(title, artist, rank)
        return path

    for i in range(16):
        hue = 0.018 + 0.0012 * i
        light = 0.26 + 0.022 * i
        r, g, b = colorsys.hls_to_rgb(hue, light, 0.92)
        image = Image.new("RGB", (480, 480), (int(r * 255), int(g * 255), int(b * 255)))
        title = f"Red Cover {i:02d}"
        save(f"{i + 1:04d}", image, title, f"Artist{i % 5}", i + 1)

    first = directory / "0001_Artist0_Red Cover 00.jpg"
    duplicate = directory / "0017_Artist0_Duplicate Red Cover.jpg"
    shutil.copyfile(first, duplicate)
    add_meta("Duplicate Red Cover", "Artist0", 1)

    for i in range(8):
        hue = 0.28 + 0.012 * i
        light = 0.28 + 0.035 * i
        r, g, b = colorsys.hls_to_rgb(hue, light, 0.85)
        image = Image.new("RGB", (480, 480), (int(r * 255), int(g * 255), int(b * 255)))
        save(f"{18 + i:04d}", image, f"Green Cover {i:02d}", f"Green Artist {i % 3}", 101 + i)

    for i in range(8):
        image = Image.new("RGB", (480, 480), (20, 20, 20))
        draw = ImageDraw.Draw(image)
        for _ in range(40):
            x, y = rng.integers(0, 420, 2)
            draw.ellipse([x, y, x + 60, y + 60], fill=tuple(int(v) for v in rng.integers(0, 255, 3)))
        save(f"{26 + i:04d}", image, f"Noise Study {i:02d}", f"Noise Artist {i}", 201 + i)

    for i in range(16):
        value = 2 + i * 3
        image = Image.new("RGB", (480, 480), (value, value, value))
        save(f"{36 + i:04d}", image, f"Black Cover {i:02d}", f"Black Artist {i % 4}", 401 + i)

    image_a = Image.new("RGB", (480, 480), "white")
    draw_a = ImageDraw.Draw(image_a)
    for y in (130, 220, 310):
        draw_a.rectangle([90, y, 390, y + 18], fill="black")
    draw_a.ellipse([175, 145, 305, 275], outline="black", width=14)
    save("0034", image_a, "Line Study A", "Mono Artist A", 301)

    image_b = Image.new("RGB", (480, 480), "white")
    draw_b = ImageDraw.Draw(image_b)
    draw_b.line([80, 80, 400, 400], fill="black", width=24)
    draw_b.line([400, 80, 80, 400], fill="black", width=24)
    draw_b.polygon([(240, 110), (350, 370), (130, 370)], outline="black", width=18)
    save("0035", image_b, "Line Study B", "Mono Artist B", 302)

    with (directory / "static.json").open("w", encoding="utf-8") as fh:
        json.dump(metadata, fh, ensure_ascii=False, indent=1)
    return metadata


def run(*args, expect=0):
    command = [PY, *map(str, args)]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", env=ENV)
    label = " ".join(str(arg) for arg in args)
    if completed.returncode != expect:
        print(completed.stdout)
        print(completed.stderr)
        raise SystemExit(f"FAIL: expected exit {expect}, got {completed.returncode}: {label}")
    print(f"OK exit={completed.returncode}: {Path(str(args[1])).name}")
    return completed


def read_json(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def assert_monotone(report: dict):
    size = report["size"]
    values = [item["LCh"][0] + 0.25 * item["LCh"][1] for item in report["items"]]
    for row in range(size):
        line = values[row * size:(row + 1) * size]
        assert all(line[i] >= line[i + 1] - 1e-6 for i in range(size - 1)), line
    for col in range(size):
        column = [values[row * size + col] for row in range(size)]
        assert all(column[i] >= column[i + 1] - 1e-6 for i in range(size - 1)), column


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="保留临时产物")
    args = parser.parse_args()

    temporary = Path(tempfile.mkdtemp(prefix="album-wall-selftest-"))
    try:
        source = temporary / "covers"
        metadata = make_fixture(source)
        print(f"Generated {len(metadata)} synthetic covers -> {source}")

        index = temporary / "index.json"
        dedup = temporary / "dedup.json"
        static = source / "static.json"
        built = run(HERE / "build_index.py", "--src", source, "--out", index, "--static", static, "--workers", 4)
        summary = json.loads(built.stdout.strip())
        assert summary["indexed"] == len(metadata), summary
        assert summary["metadata_matches"] == len(metadata), summary

        rows = read_json(index)
        sample = next(row for row in rows if row["album"] == "Red Cover 00")
        assert sample["artist"] == "Artist0"
        assert sample["album"] == "Red Cover 00"
        assert sample["rank"] == 1

        run(HERE / "dedupe.py", "--index", index, "--out", dedup, "--workers", 4)
        dedup_data = read_json(dedup)
        assert dedup_data["stats"]["dropped"] >= 1, dedup_data["stats"]
        mono_ids = {row["id"] for row in rows if row["album"] in {"Line Study A", "Line Study B"}}
        mono_groups = [
            group for group in dedup_data["groups"]
            if mono_ids & {member["id"] for member in group["members"]}
        ]
        assert not mono_groups, "distinct monochrome covers were incorrectly merged"

        for mode in ("fav", "similar", "gradient"):
            output = temporary / "nested" / "deeper" / f"wall-{mode}.jpg"
            report_path = temporary / f"wall-{mode}.json"
            run(
                HERE / "make_wall.py", "--index", index, "--dedup", dedup,
                "--color", "red", "--mode", mode, "--size", "3",
                "--out", output, "--json", report_path, "--cell", "160",
            )
            report = read_json(report_path)
            assert report["picked"] == 9
            assert report["monotone"] is True
            assert output.is_file() and output.stat().st_size > 1000
            assert all(not item["album"][:1].isdigit() for item in report["items"])
            assert_monotone(report)
            if mode == "gradient":
                assert report["algorithm"] == "hungarian"

        favourite_report = read_json(temporary / "wall-fav.json")
        assert favourite_report["filled_count"] > 0

        for mode in ("similar", "gradient"):
            output = temporary / f"black-{mode}.jpg"
            report_path = temporary / f"black-{mode}.json"
            run(
                HERE / "make_wall.py", "--index", index, "--dedup", dedup,
                "--color", "black", "--mode", mode, "--size", "3",
                "--out", output, "--json", report_path, "--cell", "160",
            )
            report = read_json(report_path)
            assert report["picked"] == 9
            assert report["monotone"] is True
            assert_monotone(report)

        auto_rows = [
            {"id": f"auto-{i}", "path": f"auto-{i}.jpg", "album": f"Auto {i}",
             "avg_lab": [50.0, 0.0, 0.0], "avg_LCh": [50.0, 35.0, 160.0]}
            for i in range(10)
        ]
        pool, core, level, rule, auto_relaxed = select_pool(
            auto_rows, "red", set(), None, True, 17, hue=(180.0, 181.0), cmin=30.0,
        )
        assert auto_relaxed is True and level > 0 and len(core) == 0 and len(pool) == 10
        strict_pool, strict_core, strict_level, strict_rule, strict_auto = select_pool(
            auto_rows, "red", set(), None, False, 17, hue=(180.0, 181.0), cmin=30.0,
        )
        assert strict_auto is False and strict_level == 0 and len(strict_core) == 0 and len(strict_pool) == 0

        no_static_index = temporary / "index-no-static.json"
        run(HERE / "build_index.py", "--src", source, "--out", no_static_index, "--workers", 4)
        run(
            HERE / "make_wall.py", "--index", no_static_index, "--dedup", dedup,
            "--color", "red", "--mode", "fav", "--size", "3",
            "--out", temporary / "no-static.jpg", expect=4,
        )

        playlist = temporary / "playlist.txt"
        playlist.write_text(
            "Track A - Artist0\n"
            "Track B - Artist0 / Artist1\n"
            "Track C - Artist1\n"
            "Track D - Artist2\n",
            encoding="utf-8",
        )
        artist_ranks = temporary / "artist-ranks.json"
        built_ranks = run(HERE / "build_favourites.py", "--playlist", playlist, "--out", artist_ranks)
        rank_summary = json.loads(built_ranks.stdout.strip())
        assert rank_summary["artists"] == 3
        ranks = read_json(artist_ranks)["artists"]
        assert ranks["Artist0"]["rank"] == 1 and ranks["Artist0"]["songs"] == 2
        assert ranks["Artist1"]["rank"] == 2 and ranks["Artist1"]["songs"] == 2
        inferred_report = temporary / "artist-ranks-wall.json"
        run(
            HERE / "make_wall.py", "--index", no_static_index, "--dedup", dedup,
            "--color", "red", "--mode", "fav", "--size", "3",
            "--artist-ranks", artist_ranks, "--out", temporary / "artist-ranks.jpg",
            "--json", inferred_report, "--cell", "160",
        )
        inferred = read_json(inferred_report)
        assert inferred["artist_rank_matches"] > 0
        assert any(item["artist_rank"] is not None for item in inferred["items"])

        manual_ranks = temporary / "manual-artist-ranks.json"
        run(
            HERE / "build_favourites.py", "--artists", "Artist2,Artist0,Artist1",
            "--out", manual_ranks,
        )
        manual = read_json(manual_ranks)["artists"]
        assert manual["Artist2"]["rank"] == 1
        assert manual["Artist0"]["rank"] == 2

        run(
            HERE / "make_wall.py", "--index", index, "--dedup", dedup,
            "--color", "black", "--mode", "similar", "--size", "3",
            "--min-clean", "0.2", "--out", temporary / "bad-black.jpg", expect=2,
        )

        run(
            HERE / "make_wall.py", "--index", index, "--dedup", dedup,
            "--color", "red", "--mode", "similar", "--size", "0",
            "--out", temporary / "bad.jpg", expect=2,
        )
        run(
            HERE / "make_wall.py", "--index", index, "--dedup", dedup,
            "--color", "red", "--mode", "similar", "--size", "3", "--hue", "bad",
            "--out", temporary / "bad.jpg", expect=2,
        )
        run(
            HERE / "make_wall.py", "--index", index, "--dedup", temporary / "missing.json",
            "--color", "red", "--mode", "similar", "--size", "3",
            "--out", temporary / "bad.jpg", expect=4,
        )
        run(
            HERE / "make_wall.py", "--index", index, "--dedup", dedup,
            "--color", "red", "--mode", "similar", "--size", "3",
            "--hue", "248-288", "--min-clean", "0.2",
            "--out", temporary / "bad.jpg", expect=2,
        )
        run(
            HERE / "make_wall.py", "--index", index, "--dedup", dedup,
            "--color", "red", "--mode", "similar", "--size", "3", "--relax", "5",
            "--out", temporary / "bad.jpg", expect=2,
        )
        bad_static = temporary / "bad-static.json"
        bad_static.write_text('{"No Such Album": {"artist": "Nobody", "rank": 1}}', encoding="utf-8")
        run(
            HERE / "build_index.py", "--src", source, "--out", temporary / "bad-static-index.json",
            "--static", bad_static, expect=4,
        )
        run(
            HERE / "make_wall.py", "--index", index, "--dedup", dedup,
            "--color", "blue", "--mode", "similar", "--size", "4",
            "--out", temporary / "nope.jpg", expect=3,
        )

        bad_source = temporary / "bad-source"
        bad_source.mkdir()
        shutil.copyfile(next(source.glob("*Red Cover 00.jpg")), bad_source / "valid.jpg")
        (bad_source / "broken.jpg").write_text("not an image", encoding="utf-8")
        bad_index = temporary / "bad-index.json"
        bad_summary = run(HERE / "build_index.py", "--src", bad_source, "--out", bad_index)
        assert json.loads(bad_summary.stdout)["failed"] == 1

        print("ALL SELF-TESTS PASSED")
        if args.keep:
            print(f"Kept test artifacts: {temporary}")
        return 0
    finally:
        if not args.keep:
            shutil.rmtree(temporary, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())