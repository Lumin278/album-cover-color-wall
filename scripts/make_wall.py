#!/usr/bin/env python3
"""Build a colour-filtered album wall with a strictly monotone 2-D layout."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from common import CliError, configure_stdio, emit_cli_error, normalize_key, parse_hue, read_json, safe_rank, write_json

BANDS = {
    "red": (15, 45), "yellow": (85, 115), "green": (115, 165), "blue": (255, 300),
    "purple": (285, 315), "orange": (52, 72), "cyan": (188, 212), "magenta": (335, 5),
}
BMIN = {"red": 30, "yellow": 25, "green": 21, "blue": 27,
        "purple": 28, "orange": 35, "cyan": 27, "magenta": 25}
RELAX = {
    "red": (8, 4), "yellow": (7, 4), "green": (8, 4), "blue": (8, 4),
    "purple": (5, 3), "orange": (6, 4), "cyan": (8, 4), "magenta": (6, 3),
}
ALIASES = {"pink": "magenta"}
CN = {"red": "红", "yellow": "黄", "green": "绿", "blue": "蓝", "black": "黑",
      "purple": "紫", "orange": "橙", "cyan": "青", "magenta": "品红", "pink": "粉",
      "custom": "自定义"}
COLORS = set(BANDS) | {"black", "pink"}
ARTIST_SPLIT = re.compile(r"\s*、\s*|\s+/\s+")


def in_band(h, lo, hi):
    lo %= 360
    hi %= 360
    return ((h >= lo) | (h <= hi)) if lo > hi else ((h >= lo) & (h <= hi))


def row_key(row: dict) -> str:
    return str(row.get("id") or row.get("path") or row.get("album") or "")


def split_artists(value: str) -> list[str]:
    return [part.strip() for part in ARTIST_SPLIT.split(str(value or "")) if part.strip()]


def primary_artist(row: dict) -> str:
    artists = split_artists(row.get("artist", ""))
    return artists[0] if artists else str(row.get("album") or row_key(row))


def load_artist_ranking(path: str) -> dict:
    payload = read_json(path, "喜爱歌手排名文件")
    if not isinstance(payload, dict):
        raise CliError("--artist-ranks 必须是 JSON 对象", 4)
    source = payload.get("artists", payload)
    if not isinstance(source, dict):
        raise CliError("--artist-ranks 的 artists 字段必须是对象", 4)
    ranking = {}
    for name, meta in source.items():
        if isinstance(meta, dict):
            raw_rank = meta.get("rank", 9999)
            songs = meta.get("songs")
        else:
            raw_rank = meta
            songs = None
        rank = safe_rank(raw_rank)
        if rank >= 9999:
            continue
        key = normalize_key(name)
        if key:
            ranking[key] = {"name": str(name), "rank": rank, "songs": songs}
    if not ranking:
        raise CliError("--artist-ranks 中没有有效排名", 4)
    return ranking


def apply_artist_ranking(rows: list[dict], ranking: dict) -> int:
    matched = 0
    for row in rows:
        candidates = []
        for artist in split_artists(row.get("artist", "")):
            hit = ranking.get(normalize_key(artist))
            if hit is not None:
                candidates.append(hit)
        if not candidates:
            continue
        best = min(candidates, key=lambda item: (item["rank"], item["name"].casefold()))
        if row.get("rank") is not None and row.get("album_rank") is None:
            row["album_rank"] = row.get("rank")
        row["artist_rank"] = best["rank"]
        row["artist_songs"] = best["songs"]
        row["matched_artist"] = best["name"]
        row["rank"] = best["rank"]
        matched += 1
    return matched


def favourite_rank(row: dict) -> float:
    if row.get("artist_rank") is not None:
        return safe_rank(row.get("artist_rank"))
    return safe_rank(row.get("rank"))


def canonical_color(color: str) -> str:
    return ALIASES.get(color, color)


def effective_rule(color: str, relax: int, hue=None, cmin=None) -> dict:
    canonical = canonical_color(color)
    if hue is not None:
        lo, hi = hue
        lo = (lo - 8 * relax) % 360
        hi = (hi + 8 * relax) % 360
        effective_cmin = (cmin if cmin is not None else 20) - 5 * relax
        return {
            "kind": "hue", "color": color, "clean_key": canonical, "relax": relax,
            "hue_lo": round(lo, 2), "hue_hi": round(hi, 2),
            "cmin": round(effective_cmin, 2),
        }
    if canonical == "black":
        return {
            "kind": "black", "color": color, "clean_key": canonical, "relax": relax,
            "max_L": 28 + 2 * relax, "max_C": 40 + 5 * relax,
            "cmin": cmin,
        }
    if canonical not in BANDS:
        raise CliError(f"未知颜色 {color}，可选：{', '.join(sorted(COLORS))}", 2)
    base_lo, base_hi = BANDS[canonical]
    hue_step, chroma_step = RELAX[canonical]
    if canonical == "magenta":
        lo = (base_lo - hue_step * relax) % 360
        hi = (base_hi + min(2 * relax, 5)) % 360
    else:
        lo = (base_lo - hue_step * relax) % 360
        hi = (base_hi + hue_step * relax) % 360
    effective_cmin = (cmin if cmin is not None else BMIN[canonical]) - chroma_step * relax
    return {
        "kind": "hue", "color": color, "clean_key": canonical, "relax": relax,
        "hue_lo": round(lo, 2), "hue_hi": round(hi, 2),
        "cmin": round(effective_cmin, 2),
    }


def build_pool(rows, color, drop, rule: dict, min_clean=None):
    pool = []
    for row in rows:
        if row_key(row) in drop or row.get("album") in drop:
            continue
        lch = row.get("avg_LCh")
        if not isinstance(lch, list) or len(lch) < 3:
            continue
        lightness, chroma, hue_value = float(lch[0]), float(lch[1]), float(lch[2])
        if rule["kind"] == "black":
            if lightness > rule["max_L"] or chroma > rule["max_C"]:
                continue
            if rule.get("cmin") is not None and chroma < rule["cmin"]:
                continue
        else:
            if chroma < rule["cmin"]:
                continue
            if not bool(in_band(np.array([hue_value]), rule["hue_lo"], rule["hue_hi"])[0]):
                continue
        if min_clean is not None:
            clean = row.get("clean", {}).get(rule["clean_key"], {}).get("clean")
            if clean is None or float(clean) < min_clean:
                continue
        pool.append(row)
    return pool


def select_pool(rows, color, drop, requested_relax, auto_relax, target, hue=None, cmin=None, min_clean=None):
    if hue is not None and min_clean is not None:
        raise CliError("--min-clean 只支持预定义色相颜色；使用 --hue 时请改用 --cmin", 2)
    canonical = canonical_color(color)
    if hue is None and canonical == "black" and min_clean is not None:
        raise CliError("黑色使用 L/C 双阈值，不支持 --min-clean；可使用 --cmin 增加彩度下限", 2)
    core_rule = effective_rule(color, 0, hue=hue, cmin=cmin)
    core_pool = build_pool(rows, color, drop, core_rule, min_clean=min_clean)
    if requested_relax is not None:
        level = requested_relax
        rule = effective_rule(color, level, hue=hue, cmin=cmin)
        return build_pool(rows, color, drop, rule, min_clean=min_clean), core_pool, level, rule, False
    if not auto_relax:
        return core_pool, core_pool, 0, core_rule, False
    for level in range(5):
        rule = effective_rule(color, level, hue=hue, cmin=cmin)
        pool = build_pool(rows, color, drop, rule, min_clean=min_clean)
        if len(pool) >= target:
            return pool, core_pool, level, rule, level > 0
    rule = effective_rule(color, 4, hue=hue, cmin=cmin)
    pool = build_pool(rows, color, drop, rule, min_clean=min_clean)
    return pool, core_pool, 4, rule, True


def lab_dist(row: dict, centre: np.ndarray) -> float:
    return float(np.linalg.norm(np.asarray(row["avg_lab"], dtype=float) - centre))


def pick_similar(pool, count, centre):
    return sorted(pool, key=lambda row: lab_dist(row, centre))[:count]


def favourite_available(pool) -> bool:
    return any(favourite_rank(row) < 9999 for row in pool)


def pick_fav(pool, count, centre):
    if not favourite_available(pool):
        raise CliError(
            "喜爱模式缺少可用排名。请先通过 --static 提供歌曲/歌手喜爱名单，"
            "或改用 similar / gradient。",
            4,
        )
    ordered = sorted(
        pool,
        key=lambda row: (
            favourite_rank(row),
            -float(row.get("artist_songs") or 0),
            str(row.get("album", "")).casefold(),
        ),
    )
    selected, seen = [], set()
    for row in ordered:
        artist = normalize_key(primary_artist(row))
        if artist in seen:
            continue
        seen.add(artist)
        selected.append(row)
        if len(selected) == count:
            break
    favourite_count = len(selected)
    if favourite_count < count:
        chosen = {row_key(row) for row in selected}
        rest = [row for row in pool if row_key(row) not in chosen]
        for row in pick_similar(rest, count - favourite_count, centre):
            filled = dict(row)
            filled["_filled"] = True
            selected.append(filled)
    return selected, favourite_count


def hungarian_min_cost(cost) -> list[int]:
    """Rectangular Hungarian assignment for rows <= columns. Returns row->column."""
    matrix = np.asarray(cost, dtype=float)
    if matrix.ndim != 2:
        raise CliError("匈牙利分配代价矩阵必须是二维", 4)
    n, m = matrix.shape
    if n == 0:
        return []
    if n > m:
        raise CliError("匈牙利分配要求目标数不能多于候选数", 4)
    u = np.zeros(n + 1, dtype=float)
    v = np.zeros(m + 1, dtype=float)
    p = np.zeros(m + 1, dtype=int)
    way = np.zeros(m + 1, dtype=int)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = np.full(m + 1, np.inf)
        used = np.zeros(m + 1, dtype=bool)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = np.inf
            j1 = 0
            for j in range(1, m + 1):
                if used[j]:
                    continue
                current = matrix[i0 - 1, j - 1] - u[i0] - v[j]
                if current < minv[j]:
                    minv[j] = current
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            if not np.isfinite(delta):
                raise CliError("匈牙利分配失败：没有可用候选", 4)
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    assignment = [-1] * n
    for j in range(1, m + 1):
        if p[j] > 0:
            assignment[p[j] - 1] = j - 1
    if any(column < 0 for column in assignment):
        raise CliError("匈牙利分配失败：存在未分配目标", 4)
    return assignment


def pick_gradient(pool, count, centre):
    """Globally minimise anchor cost with the Hungarian algorithm."""
    pool_l = np.asarray([float(row["avg_LCh"][0]) for row in pool], dtype=float)
    low, high = np.quantile(pool_l, [0.05, 0.95])
    if count == 1 or abs(high - low) < 1e-9:
        anchors = np.full(count, float(pool_l.mean()))
    else:
        anchors = np.linspace(float(high), float(low), count)
    hue_centre = float(np.median([float(row["avg_LCh"][2]) for row in pool]))
    cost = np.empty((count, len(pool)), dtype=float)
    for i, anchor in enumerate(anchors):
        for j, row in enumerate(pool):
            lightness = float(row["avg_LCh"][0])
            hue = float(row["avg_LCh"][2])
            hue_delta = abs((hue - hue_centre + 180.0) % 360.0 - 180.0)
            cost[i, j] = abs(lightness - anchor) + 0.15 * hue_delta + 0.05 * lab_dist(row, centre)
    assignment = hungarian_min_cost(cost)
    return [pool[column] for column in assignment]


def metric(row: dict) -> float:
    return float(row["avg_LCh"][0]) + 0.25 * float(row["avg_LCh"][1])


def energy(grid) -> float:
    total = 0.0
    rows = len(grid)
    for i in range(rows):
        for j in range(len(grid[i])):
            current = np.asarray(grid[i][j]["avg_lab"], dtype=float)
            if j + 1 < len(grid[i]):
                total += float(np.linalg.norm(current - np.asarray(grid[i][j + 1]["avg_lab"], dtype=float)))
            if i + 1 < rows:
                total += float(np.linalg.norm(current - np.asarray(grid[i + 1][j]["avg_lab"], dtype=float)))
    return total


def monotone(grid, tolerance=1e-6) -> bool:
    size = len(grid)
    for i in range(size):
        for j in range(size - 1):
            if metric(grid[i][j + 1]) > metric(grid[i][j]) + tolerance:
                return False
    for j in range(size):
        for i in range(size - 1):
            if metric(grid[i + 1][j]) > metric(grid[i][j]) + tolerance:
                return False
    return True


def smooth_2d(items, size):
    """Optimise vertical swaps without violating row/column monotonicity."""
    ordered = sorted(items, key=metric, reverse=True)
    grid = [ordered[i * size:(i + 1) * size] for i in range(size)]
    best = energy(grid)
    improved = True
    while improved:
        improved = False
        for column in range(size):
            for row in range(size - 1):
                grid[row][column], grid[row + 1][column] = grid[row + 1][column], grid[row][column]
                candidate = energy(grid)
                if monotone(grid) and candidate < best - 1e-6:
                    best = candidate
                    improved = True
                else:
                    grid[row][column], grid[row + 1][column] = grid[row + 1][column], grid[row][column]
    return [row for line in grid for row in line], best


def load_font(size=18):
    candidates = (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/System/Library/Fonts/PingFang.ttc",
    )
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def fit_text(draw, text: str, font, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    ellipsis = "..."
    candidate = text
    while candidate and draw.textlength(candidate + ellipsis, font=font) > max_width:
        candidate = candidate[:-1]
    return (candidate.rstrip() + ellipsis) if candidate else ellipsis


def render(items, size, out, cell=400):
    line = 28
    bar = line * size + 10
    width = cell * size
    canvas = Image.new("RGB", (width, width + bar), "#121212")
    for index, row in enumerate(items):
        path = Path(row["path"])
        if not path.is_file():
            raise CliError(f"选中的封面文件不存在：{path}", 4)
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image = ImageOps.fit(image, (cell, cell), method=Image.Resampling.LANCZOS)
        canvas.paste(image, ((index % size) * cell, (index // size) * cell))

    draw = ImageDraw.Draw(canvas)
    font = load_font(18)
    for index, row in enumerate(items):
        album = str(row.get("album") or row.get("source_stem") or row.get("source") or "Unknown")
        suffix = " *" if row.get("_filled") else ""
        tag = fit_text(draw, f"{index + 1}. {album}{suffix}", font, cell - 16)
        draw.text(
            (8 + (index % size) * cell, width + 6 + (index // size) * line),
            tag,
            font=font,
            fill="#dddddd",
        )
    output = Path(out)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output, "JPEG", quality=93, optimize=True)
    except OSError as exc:
        raise CliError(f"无法保存输出图：{output}：{exc}", 4) from exc
    return output


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="从索引生成专辑色墙")
    parser.add_argument("--index", required=True)
    parser.add_argument("--dedup")
    parser.add_argument("--artist-ranks", help="build_favourites.py 生成的歌手喜爱排名 JSON")
    parser.add_argument("--color", required=True)
    parser.add_argument("--mode", choices=["fav", "similar", "gradient"], default="fav")
    parser.add_argument("--size", type=int, default=4)
    parser.add_argument("--relax", type=int, default=None, help="手动指定放宽级别；指定后不自动放宽")
    parser.add_argument("--no-auto-relax", action="store_true", help="只使用严格核心色，不自动放宽")
    parser.add_argument("--out", default="wall.jpg")
    parser.add_argument("--json")
    parser.add_argument("--cell", type=int, default=400)
    parser.add_argument("--skip", help="黑名单 JSON：专辑名数组")
    parser.add_argument("--hue", help="自定义色相带，如 248-288")
    parser.add_argument("--cmin", type=float)
    parser.add_argument("--min-clean", type=float)
    args = parser.parse_args()

    try:
        if args.size < 1 or args.size > 10:
            raise CliError("--size 必须在 1 到 10 之间", 2)
        if args.cell < 96 or args.cell > 1600:
            raise CliError("--cell 必须在 96 到 1600 像素之间", 2)
        if args.relax is not None and (args.relax < 0 or args.relax > 4):
            raise CliError("--relax 必须在 0 到 4 之间", 2)
        if args.cmin is not None and args.cmin < 0:
            raise CliError("--cmin 不能为负数", 2)
        if args.min_clean is not None and not (-1 <= args.min_clean <= 1):
            raise CliError("--min-clean 必须在 -1 到 1 之间", 2)

        rows = read_json(args.index, "索引文件")
        if not isinstance(rows, list) or not rows:
            raise CliError("索引必须是非空 JSON 数组", 4)
        valid_rows, missing = [], []
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                raise CliError(f"索引第 {i + 1} 条不是对象", 4)
            required = ("path", "album", "avg_lab", "avg_LCh")
            if any(key not in row for key in required):
                raise CliError(f"索引第 {i + 1} 条缺少必要字段：{', '.join(required)}", 4)
            if not Path(row["path"]).is_file():
                missing.append(row.get("id") or row.get("path"))
                continue
            valid_rows.append(row)
        if missing:
            print(f"警告：{len(missing)} 张索引图片不存在，已跳过。", file=sys.stderr)

        artist_matches = None
        if args.artist_ranks:
            ranking = load_artist_ranking(args.artist_ranks)
            artist_matches = apply_artist_ranking(valid_rows, ranking)
            if artist_matches == 0:
                raise CliError(
                    "--artist-ranks 与索引中的 artist 字段没有任何匹配；"
                    "请检查歌手名，或使用 --filename-regex 修正索引解析。",
                    4,
                )
            if artist_matches < len(valid_rows):
                print(
                    f"提示：喜爱歌手覆盖 {artist_matches}/{len(valid_rows)} 张封面；"
                    "未覆盖项只会在喜爱模式中作为补位候选。",
                    file=sys.stderr,
                )

        drop = set()
        if args.dedup:
            dedup = read_json(args.dedup, "去重文件")
            if not isinstance(dedup, dict):
                raise CliError("--dedup 必须是 JSON 对象", 4)
            for key in ("drop", "invalid"):
                value = dedup.get(key, [])
                if not isinstance(value, list):
                    raise CliError(f"去重文件字段 {key} 必须是数组", 4)
                drop.update(str(item) for item in value)

        if args.skip:
            skipped = read_json(args.skip, "黑名单文件")
            if not isinstance(skipped, list) or not all(isinstance(item, str) for item in skipped):
                raise CliError("--skip 必须是专辑名字符串数组", 4)
            drop.update(skipped)

        hue = parse_hue(args.hue) if args.hue else None
        need = args.size * args.size
        auto_target = need + max(8, (need + 1) // 2)
        auto_relax = not args.no_auto_relax and args.relax is None
        pool, core_pool, relax_level, active_rule, auto_relaxed = select_pool(
            valid_rows, args.color, drop, args.relax, auto_relax, auto_target,
            hue=hue, cmin=args.cmin, min_clean=args.min_clean,
        )
        if auto_relaxed and relax_level > 0:
            print(
                f"提示：{CN.get(args.color, '该颜色')}严格核心池只有 {len(core_pool)} 张，"
                f"已自动放宽到第 {relax_level} 级，最终池 {len(pool)} 张。",
                file=sys.stderr,
            )
        if len(pool) < need:
            payload = {
                "error": "insufficient",
                "color": args.color,
                "available": len(pool),
                "core_available": len(core_pool),
                "need": need,
                "auto_relax_target": auto_target,
                "relax_level": relax_level,
                "effective_rule": active_rule,
                "message": f"{CN.get(args.color, '该颜色')}色即使放宽后也只有 {len(pool)} 张，凑不满 {args.size}×{args.size}={need} 格",
            }
            print(json.dumps(payload, ensure_ascii=False))
            return 3
        if auto_relaxed and relax_level == 4 and len(pool) < auto_target:
            print(
                f"提示：已到第 4 级放宽，池 {len(pool)} 张，未达到建议余量 {auto_target}，"
                "但仍足够本次出墙。",
                file=sys.stderr,
            )

        labs = np.asarray([row["avg_lab"] for row in pool], dtype=float)
        centre = np.median(labs, axis=0)
        algorithm = args.mode
        if args.mode == "similar":
            selected = pick_similar(pool, need, centre)
            favourite_count = None
        elif args.mode == "gradient":
            selected = pick_gradient(pool, need, centre)
            favourite_count = None
            algorithm = "hungarian"
        else:
            selected, favourite_count = pick_fav(pool, need, centre)
            algorithm = "favourite"

        flat, total_energy = smooth_2d(selected, args.size)
        selected_labs = np.asarray([row["avg_lab"] for row in flat], dtype=float)
        uniformity = float(np.linalg.norm(selected_labs - selected_labs.mean(0), axis=1).mean())
        if not monotone([flat[i * args.size:(i + 1) * args.size] for i in range(args.size)]):
            raise CliError("内部排序未满足单调约束，已停止出图", 1)

        output = render(flat, args.size, args.out, args.cell)
        report = {
            "color": args.color,
            "mode": args.mode,
            "algorithm": algorithm,
            "size": args.size,
            "pool": len(pool),
            "core_pool": len(core_pool),
            "relax_level": relax_level,
            "auto_relaxed": auto_relaxed,
            "auto_relax_target": auto_target,
            "effective_rule": active_rule,
            "picked": len(flat),
            "out": str(output.resolve()),
            "uniformity": round(uniformity, 2),
            "energy": round(total_energy, 1),
            "light_span": round(float(selected_labs[:, 0].max() - selected_labs[:, 0].min()), 1),
            "fav_filled": favourite_count,
            "filled_count": sum(1 for row in flat if row.get("_filled")),
            "monotone": True,
            "artist_rank_matches": artist_matches,
            "items": [{
                "pos": i + 1,
                "album": row.get("album", ""),
                "artist": row.get("artist", ""),
                "rank": row.get("rank"),
                "album_rank": row.get("album_rank"),
                "artist_rank": row.get("artist_rank"),
                "artist_songs": row.get("artist_songs"),
                "matched_artist": row.get("matched_artist"),
                "LCh": row["avg_LCh"],
                "filled": bool(row.get("_filled")),
            } for i, row in enumerate(flat)],
        }
        if args.json:
            write_json(args.json, report, indent=1)
        print(json.dumps({key: value for key, value in report.items() if key != "items"}, ensure_ascii=False))
        return 0
    except CliError as exc:
        return emit_cli_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())