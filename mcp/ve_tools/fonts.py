"""Windows CJK font discovery — one list, one override, one place.

Every renderer in the kit needs a *concrete font file* (ffmpeg `drawtext
fontfile=`, PIL `ImageFont.truetype`, libass metrics in `subtitle_style`) or a
*family name* (`subtitles=...:force_style=FontName=`). 本插件只在 Windows 上
运行: 系统自带微软雅黑/黑体/宋体, 所以这里只搜 Windows 字体目录。

Search order:

1. ``VE_FONT_DIRS`` / ``VIDEO_EDIT_FONT_DIRS`` — explicit override, wins over
   everything (already the convention in `render.py` / `video_observe.py`).
2. Windows system font dirs — SystemRoot 下的 Fonts 目录, 以及每用户安装的
   LOCALAPPDATA Microsoft/Windows/Fonts (Windows 10+ "仅为我安装").

Nothing here imports a third-party package at module level: the sys-setup
doctor imports this module (inside the plugin venv) to probe the font
situation, and font discovery must stay importable from stdlib-only callers.
"""
from __future__ import annotations

import functools
import os
from pathlib import Path

_PLATFORM_DIRS = [
    os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Fonts"),
    # Per-user installs (Windows 10+ "Install for me only")
    os.path.join(os.environ.get("LOCALAPPDATA", ""),
                 "Microsoft", "Windows", "Fonts"),
]

# Filenames to prefer per weight, best first — Windows 自带 CJK 字体
# (微软雅黑/黑体/宋体) 加上常见的 Noto/思源手动安装。regular 会兜底给
# 只有一个 CJK face 的机器 (libass 自己合成粗体)。
_WEIGHT_FILES = {
    "black": ["NotoSansSC-900.ttf", "NotoSansCJK-Black.ttc", "NotoSansCJKsc-Black.otf"],
    "bold": ["NotoSansSC-700.ttf", "NotoSansCJK-Bold.ttc", "NotoSansCJKsc-Bold.otf",
             "SourceHanSansSC-Bold.otf", "msyhbd.ttc", "simhei.ttf"],
    "medium": ["NotoSansSC-500.ttf", "NotoSansCJK-Medium.ttc", "NotoSansCJKsc-Medium.otf"],
    "regular": ["NotoSansSC-400.ttf",
                "NotoSansCJK-Regular.ttc", "NotoSansCJKsc-Regular.otf",
                "NotoSansSC-Regular.otf", "SourceHanSansSC-Regular.otf",
                "msyh.ttc", "simhei.ttf", "simsun.ttc", "Deng.ttf"],
}

# Last resort: any font file whose name looks like a CJK face. Matched against
# the lowercased stem so "NotoSansCJKsc-Regular" and "msyhl" both land.
_CJK_NAME_HINTS = ("notosanscjk", "notoserifcjk", "notosanssc", "notosanstc",
                   "sourcehansans", "sourcehanserif", "msyh", "msjh", "yahei",
                   "simhei", "simsun", "simkai", "deng", "fangsong", "kaiti",
                   "arialunicode", "unifont")

_FONT_SUFFIXES = {".ttf", ".ttc", ".otf", ".otc"}

# stem prefix -> the family name libass / DirectWrite answers to.
# Used for `force_style=FontName=`, where a file path is not accepted.
_FAMILY_BY_STEM = (
    ("notosanscjk", "Noto Sans CJK SC"),
    ("notosanssc", "Noto Sans SC"),
    ("notosanstc", "Noto Sans TC"),
    ("sourcehansans", "Source Han Sans SC"),
    ("msyhbd", "Microsoft YaHei"),
    ("msyh", "Microsoft YaHei"),
    ("msjh", "Microsoft JhengHei"),
    ("simhei", "SimHei"),
    ("simsun", "SimSun"),
    ("deng", "DengXian"),
)

# Family to ask libass for when no concrete file was found: Windows 自带雅黑,
# DirectWrite 直接解析。
_DEFAULT_FAMILY = "Microsoft YaHei"


def override_dirs() -> list[Path]:
    """Dirs from VE_FONT_DIRS / VIDEO_EDIT_FONT_DIRS (os.pathsep-separated)."""
    raw = (os.environ.get("VE_FONT_DIRS")
           or os.environ.get("VIDEO_EDIT_FONT_DIRS") or "")
    return [Path(p.strip()).expanduser() for p in raw.split(os.pathsep) if p.strip()]


def font_dirs() -> list[Path]:
    """Every directory worth searching, highest priority first.

    Not cached: VE_FONT_DIRS is read per call so a tool can point at a font
    directory it just materialised (and so the doctor's advice takes effect
    without restarting the MCP server).
    """
    dirs = [*override_dirs(),
            *(Path(p) for p in _PLATFORM_DIRS if p)]
    seen: set[str] = set()
    out: list[Path] = []
    for d in dirs:
        key = str(d)
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def _find_named(name: str, dirs: list[Path]) -> str | None:
    """Locate one filename: direct child first, then a recursive walk."""
    for directory in dirs:
        candidate = directory / name
        if candidate.is_file():
            return str(candidate)
    for directory in dirs:
        try:
            if not directory.is_dir():
                continue
            for hit in directory.rglob(name):
                if hit.is_file():
                    return str(hit)
        except OSError:          # unreadable mount / permission — not our problem
            continue
    return None


def _scan_by_hint(dirs: list[Path], prefer_bold: bool) -> str | None:
    for directory in dirs:
        try:
            if not directory.is_dir():
                continue
            files = [p for p in sorted(directory.rglob("*"))
                     if p.is_file() and p.suffix.lower() in _FONT_SUFFIXES
                     and any(h in p.stem.lower() for h in _CJK_NAME_HINTS)]
        except OSError:
            continue
        if not files:
            continue
        if prefer_bold:
            bold = [p for p in files if "bold" in p.stem.lower() or "bd" in p.stem.lower()]
            if bold:
                return str(bold[0])
        return str(files[0])
    return None


@functools.lru_cache(maxsize=8)
def _find_cjk_font(weight: str, dirs_key: tuple[str, ...]) -> str | None:
    dirs = [Path(d) for d in dirs_key]
    names = _WEIGHT_FILES.get(weight) or _WEIGHT_FILES["regular"]
    # Fall through to regular: a machine with only one CJK face should still
    # render bold text (libass synthesises the weight) rather than fail.
    for name in [*names, *(n for n in _WEIGHT_FILES["regular"] if n not in names)]:
        hit = _find_named(name, dirs)
        if hit:
            return hit
    return _scan_by_hint(dirs, prefer_bold=weight in ("bold", "black"))


def find_cjk_font(weight: str = "regular") -> str | None:
    """A concrete CJK-capable font file, or None if the machine has none.

    Never guesses a path that does not exist: callers that need a hard failure
    (subtitle burn-in) can raise on None, and the doctor reports it as a
    missing requirement with an install command.
    """
    return _find_cjk_font((weight or "regular").lower(),
                          tuple(str(d) for d in font_dirs()))


def family_of_file(font_file: str | Path) -> str | None:
    """Family name for a font file, by filename."""
    stem = Path(font_file).stem.lower()
    for prefix, family in _FAMILY_BY_STEM:
        if stem.startswith(prefix) or prefix in stem:
            return family
    return None


def ass_font_name() -> str | None:
    """Family name to put in `force_style=FontName=`, or None if the machine has
    no CJK font at all (caller should then skip burn-in rather than render tofu).

    The family of whatever concrete file we found first, then Windows' stock
    face — libass goes through DirectWrite, so a family name resolves there.
    """
    found = find_cjk_font("regular")
    if found:
        return family_of_file(found) or _DEFAULT_FAMILY
    return None


def covers_cjk(font_file: str | Path) -> bool | None:
    """Does this font actually have CJK glyphs? None = cannot tell.

    Reading the cmap is the only honest check. fontTools is imported lazily so
    this module stays usable before dependencies are installed.
    """
    try:
        from fontTools.ttLib import TTFont

        with TTFont(str(font_file), fontNumber=0, lazy=True) as font:
            cmap = font.getBestCmap()
    except Exception:
        return None
    # 你好 + 一 (CJK Unified Ideographs) — a face without these cannot render
    # Chinese subtitles no matter what anything claims.
    return all(ord(ch) in cmap for ch in "你好一")


def diagnose() -> dict:
    """Everything the env doctor needs in one call (no printing, no fixing)."""
    dirs = font_dirs()
    regular = find_cjk_font("regular")
    bold = find_cjk_font("bold")
    return {"platform": "windows",
            "override_dirs": [str(d) for d in override_dirs()],
            "searched_dirs": [str(d) for d in dirs],
            "existing_dirs": [str(d) for d in dirs if d.is_dir()],
            "regular": regular,
            "bold": bold,
            "covers_cjk": covers_cjk(regular) if regular else None,
            "ass_font_name": ass_font_name(),
            "default_family": _DEFAULT_FAMILY}
