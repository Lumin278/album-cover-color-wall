#!/usr/bin/env python3
"""Conservative perceptual de-duplication for indexed album covers."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from common import CliError, configure_stdio, emit_cli_error, read_json, safe_rank, write_json

N = 32
_K = np.arange(8)[:, None]
_N = np.arange(N)[None, :]
DCT = np.cos(np.pi * (2 * _N + 1) * _K / (2 * N)) * np.where(
    _K == 0, np.sqrt(1 / N), np.sqrt(2 / N)
)


def popcount(value: int) -> int:
    method = getattr(int, "bit_count", None)
    return method(value) if method else bin(value).count("1")


def fingerprint(path: str) -> dict:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(path)
    digest = hashlib.md5()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    with Image.open(p) as source:
        gray = ImageOps.exif_transpose(source).convert("L").resize(
            (N, N), Image.Resampling.LANCZOS
        )
    arr = np.asarray(gray, dtype=np.float32) / 255.0
    coeff = (DCT @ arr @ DCT.T).flatten()
    median = float(np.median(coeff[1:]))
    bits = 0
    for index, value in enumerate(coeff):
        if float(value) > median:
            bits |= 1 << index
    return {"md5": digest.hexdigest(), "phash": bits, "gray": arr}


def structural_metrics(left: np.ndarray, right: np.ndarray) -> tuple[float, float]:
    x = left.astype(np.float64).ravel()
    y = right.astype(np.float64).ravel()
    xm, ym = x - x.mean(), y - y.mean()
    denominator = float(np.sqrt((xm * xm).sum() * (ym * ym).sum()))
    correlation = float((xm * ym).sum() / denominator) if denominator > 1e-12 else 0.0
    mae = float(np.abs(x - y).mean())
    return correlation, mae


def row_id(row: dict, index: int) -> str:
    return str(row.get("id") or row.get("path") or row.get("album") or f"row-{index}")


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="对专辑封面索引进行保守去重")
    parser.add_argument("--index", required=True)
    parser.add_argument("--out", default="dedup.json")
    parser.add_argument("--th", type=int, default=6, help="pHash 汉明距离上限，默认 6")
    parser.add_argument("--cdist", type=float, default=12.0, help="Lab 平均色距离上限")
    parser.add_argument("--min-corr", type=float, default=0.92, help="灰度结构相关系数下限")
    parser.add_argument("--max-mae", type=float, default=0.08, help="灰度平均绝对误差上限")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    try:
        if args.th < 0 or args.th > 64:
            raise CliError("--th 必须在 0 到 64 之间", 2)
        if args.cdist < 0:
            raise CliError("--cdist 不能为负数", 2)
        if args.min_corr < 0 or args.min_corr > 1:
            raise CliError("--min-corr 必须在 0 到 1 之间", 2)
        if args.max_mae < 0 or args.max_mae > 1:
            raise CliError("--max-mae 必须在 0 到 1 之间", 2)
        if args.workers < 1 or args.workers > 64:
            raise CliError("--workers 必须在 1 到 64 之间", 2)

        rows = read_json(args.index, "索引文件")
        if not isinstance(rows, list) or not rows:
            raise CliError("索引必须是非空 JSON 数组", 4)
        for i, row in enumerate(rows):
            if not isinstance(row, dict) or "path" not in row or "avg_lab" not in row:
                raise CliError(f"索引第 {i + 1} 条缺少 path 或 avg_lab", 4)

        def safe_fingerprint(row: dict):
            try:
                return row, fingerprint(row["path"]), None
            except Exception as exc:
                return row, None, f"{type(exc).__name__}: {exc}"

        records, invalid = [], []
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            for row, data, error in executor.map(safe_fingerprint, rows):
                if error:
                    invalid.append({"id": row_id(row, len(records)), "path": row.get("path", ""), "error": error})
                else:
                    records.append((row, data))
        if not records:
            raise CliError("所有图片都无法计算指纹，未生成去重结果", 4)

        n = len(records)
        parent = {i: i for i in range(n)}

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(left: int, right: int) -> None:
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parent[right_root] = left_root

        near_pairs = 0
        for i in range(n):
            lab_i = np.asarray(records[i][0]["avg_lab"], dtype=float)
            for j in range(i + 1, n):
                if records[i][1]["md5"] == records[j][1]["md5"]:
                    union(i, j)
                    continue
                hamming = popcount(records[i][1]["phash"] ^ records[j][1]["phash"])
                if hamming > args.th:
                    continue
                lab_j = np.asarray(records[j][0]["avg_lab"], dtype=float)
                if float(np.linalg.norm(lab_i - lab_j)) > args.cdist:
                    continue
                correlation, mae = structural_metrics(records[i][1]["gray"], records[j][1]["gray"])
                if correlation < args.min_corr or mae > args.max_mae:
                    continue
                near_pairs += 1
                union(i, j)

        components: dict[int, list[int]] = {}
        for i in range(n):
            components.setdefault(find(i), []).append(i)

        groups, drop = [], []
        for component in components.values():
            if len(component) < 2:
                continue
            ordered = sorted(
                component,
                key=lambda i: (safe_rank(records[i][0].get("rank")), str(records[i][0].get("album", "")).casefold()),
            )
            representative = ordered[0]
            members = []
            for i in ordered:
                members.append({
                    "id": row_id(records[i][0], i),
                    "album": records[i][0].get("album", ""),
                    "source": records[i][0].get("source", ""),
                    "path": records[i][0].get("path", ""),
                })
            hashes = {records[i][1]["md5"] for i in ordered}
            correlations = []
            dists = []
            for i in ordered[1:]:
                correlations.append(structural_metrics(records[representative][1]["gray"], records[i][1]["gray"])[0])
                dists.append(popcount(records[representative][1]["phash"] ^ records[i][1]["phash"]))
            groups.append({
                "representative": row_id(records[representative][0], representative),
                "members": members,
                "evidence": {
                    "md5_identical": len(hashes) == 1,
                    "max_hamming_to_representative": max(dists) if dists else 0,
                    "min_correlation_to_representative": round(min(correlations), 4) if correlations else 1.0,
                },
            })
            drop.extend(row_id(records[i][0], i) for i in ordered[1:])

        output = {
            "drop": sorted(drop),
            "invalid": invalid,
            "groups": sorted(groups, key=lambda g: g["representative"].casefold()),
            "stats": {
                "indexed": len(rows),
                "fingerprinted": n,
                "invalid": len(invalid),
                "groups": len(groups),
                "dropped": len(drop),
                "near_pairs": near_pairs,
                "thresholds": {
                    "hamming": args.th,
                    "lab_distance": args.cdist,
                    "min_correlation": args.min_corr,
                    "max_mae": args.max_mae,
                },
            },
        }
        destination = write_json(args.out, output, indent=1)
        print(json.dumps({"ok": True, **output["stats"], "out": str(destination)}, ensure_ascii=False))
        if invalid:
            print(f"警告：{len(invalid)} 张图片指纹失败，已列入 invalid。", file=sys.stderr)
        return 0
    except CliError as exc:
        return emit_cli_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())