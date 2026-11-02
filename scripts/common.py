#!/usr/bin/env python3
"""Shared CLI helpers for the album-cover-color-wall skill."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path


class CliError(Exception):
    def __init__(self, message: str, code: int = 4, payload: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.payload = payload or {}


def configure_stdio() -> None:
    """Use UTF-8 even when the Windows console defaults to GBK."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def read_json(path: str | os.PathLike[str], label: str):
    p = Path(path)
    if not p.is_file():
        raise CliError(f"{label}不存在或不是文件：{p}", 4)
    try:
        with p.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except UnicodeDecodeError as exc:
        raise CliError(f"{label}不是 UTF-8 编码：{p}") from exc
    except json.JSONDecodeError as exc:
        raise CliError(f"{label} JSON 格式错误：{p}（第 {exc.lineno} 行）") from exc
    except OSError as exc:
        raise CliError(f"无法读取{label}：{p}：{exc}") from exc


def write_json(path: str | os.PathLike[str], data, indent: int | None = None) -> Path:
    """Write JSON atomically so an interrupted run cannot leave half a file."""
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=p.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=indent)
                fh.write("\n")
            os.replace(tmp_name, p)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except OSError as exc:
        raise CliError(f"无法写入文件：{p}：{exc}") from exc
    return p


def parse_hue(text: str) -> tuple[float, float]:
    match = re.fullmatch(
        r"\s*(-?(?:\d+(?:\.\d*)?|\.\d+))\s*[-–—]\s*(-?(?:\d+(?:\.\d*)?|\.\d+))\s*",
        text,
    )
    if not match:
        raise CliError("色相带格式应为 LO-HI，例如 248-288", 2)
    lo = float(match.group(1)) % 360
    hi = float(match.group(2)) % 360
    if abs(lo - hi) < 1e-9:
        raise CliError("色相带起止值不能相同", 2)
    return lo, hi


def normalize_key(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def safe_rank(value, default=9999.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if number == number else float(default)


def emit_cli_error(exc: CliError) -> int:
    configure_stdio()
    payload = {"error": "invalid_input", "message": exc.message}
    payload.update(exc.payload)
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
    return exc.code