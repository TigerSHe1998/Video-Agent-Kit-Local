#!/usr/bin/env python3
"""video-agent-kit 环境体检: 逐项探测 → 输出「缺什么 / 挂哪个能力 / 本平台怎么装」。

跨平台 (macOS / Windows / Linux)。设计约束照 video2code 的同名脚本:
- **要求先于命令**: 每项先说清"需要什么"(名字 + 版本线), 再按当前平台给示例命令。
  用户机器长什么样这里不知道 (有没有 brew/winget、有没有管理员权限、是不是 conda
  环境), 所以命令是示例不是规定 —— 只有要求是硬的。
- **探测与修复分离**: 默认只读探测; `--fix` 才动手, 且只跑标了 auto 的项 (用户态、
  幂等)。系统级软件 (ffmpeg / 字体 / 系统库) 永远只打印, 不自动执行。
- **单一事实源**: `/env-check` 命令 (commands/env-check.md) 与本脚本的检查项
  同源, 依赖清单不许两处各写一份 (漂移过一次就永远漂移)。
- **探测顺序即依赖顺序**: 没装 ffmpeg 就不必再问它有没有 libass —— 后置项标
  `needs`, 前置失败则整项跳过 (报 skip 而不是伪失败), 免得一个根因炸出五条噪音。
- **"装了" ≠ "能用"**: 这个插件挂在"装了但不带某个 feature"上的次数比挂在"没装"上
  多 —— conda/静态包的 ffmpeg 常常没有 libass(烧不了字幕)或没有 libmp3lame;
  fc-match 问它要中文字体会把 DejaVu 交给你(渲染出豆腐块但编码成功)。所以
  ffmpeg 查编码器/滤镜、字体查 cmap 真覆盖, 不只查 PATH 上在不在。

用法:
  env_doctor.py            # 全量探测 + 修复计划
  env_doctor.py --cheap    # 只跑毫秒级项 (无网络)
  env_doctor.py --json     # 机器可读
  env_doctor.py --fix      # 探测后自动装 auto 项, 再复测这些项
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT")
                   or Path(__file__).resolve().parents[3])
PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR")
                   or os.environ.get("VE_PROJECT_DIR") or Path.cwd())
REQUIREMENTS = PLUGIN_ROOT / "requirements.txt"
PY = sys.executable or "python3"
# 命令行里用 PYQ: Windows 解释器路径常含空格, do_fix 又走 shell=True
PYQ = f'"{PY}"' if " " in PY else PY

OS = ("macos" if sys.platform == "darwin"
      else "windows" if os.name == "nt" else "linux")

# ── 国内镜像: 默认走, 不是"备选" ──────────────────────────────────────────────
# 面向的是普通用户, 官方源在国内经常几十 KB/s 甚至直接超时, 而 pip 的超时表现是
# "装了十分钟然后失败", 最容易被误判成别的毛病。所以默认就用镜像, 出问题再回官方源
# (VE_PIP_INDEX 可覆盖)。
PIP_TSINGHUA = "https://pypi.tuna.tsinghua.edu.cn/simple"
PIP_ALIYUN = "https://mirrors.aliyun.com/pypi/simple/"
PIP_INDEX = os.environ.get("VE_PIP_INDEX", PIP_TSINGHUA)

# ffmpeg / 字体的装法分平台, 国内都建议走镜像 —— 但不同包管理器换镜像的方式差很多:
#   brew   → 设 HOMEBREW_BOTTLE_DOMAIN (bottle 从 GitHub 下, 国内很慢/失败)
#   apt    → 换 sources.list 里的源 (改的是系统全局配置, 不是装一次就完)
#   conda  → 指定 channel URL, 最干净 (无 root、不动系统包管理器)
# Windows 上 winget 没有镜像概念, ffmpeg 静态包来自 gyan.dev, 可能慢 —— 用 conda 绕开。
HOMEBREW_BOTTLE = "https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles"
CONDA_FORGE_TSINGHUA = "https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge"

# 渲染要落 preview/final mp4 + 抽帧 contact sheet, 空间不够是"渲到一半炸"。
MIN_FREE_GB = 5


def by_os(**variants: list[str]) -> list[str]:
    """按当前平台取一份修复示例; 缺本平台条目时回落 any。"""
    return variants.get(OS) or variants.get("any") or []


def pip_install(*specs: str, no_deps: bool = False) -> str:
    quoted = " ".join(f'"{s}"' if any(c in s for c in "<>=") else s for s in specs)
    return f"{PYQ} -m pip install -i {PIP_INDEX}{' --no-deps' if no_deps else ''} {quoted}"


PIP_MIRROR_NOTES = [
    f"# 上面用的是清华镜像; 阿里云: -i {PIP_ALIYUN}",
    f"# 想一劳永逸设成默认 (改的是全局 pip 配置, 影响这台机器上所有项目, 先问用户):",
    f"#   {PYQ} -m pip config set global.index-url {PIP_INDEX}",
    "# 镜像同步滞后/缺包时才回官方源: 去掉 -i 参数重试",
]


# ── 检查项模型 ───────────────────────────────────────────────────────────────
@dataclass
class Check:
    id: str
    label: str
    why: str                              # 缺了会挂掉哪个能力 (给模型/用户判断优先级)
    probe: Callable[[], tuple[bool, str]]  # → (ok, 细节一行)
    need: str = ""                         # 硬要求一行 ("装什么、什么版本线")
    fix: list[str] = field(default_factory=list)   # 本平台示例命令 (可空)
    cheap: bool = False                   # 毫秒级、不碰网络 → /env-check 可快速重跑
    auto: bool = False                    # --fix 允许自动执行 (只用户态、幂等)
    slow: bool = False                    # 修复耗时以分钟计, 需要长 timeout
    needs: str = ""                       # 前置检查项 id; 前置失败则本项 skip
    platforms: tuple[str, ...] = ()       # 限定平台 (空 = 全平台)
    env: dict[str, str] = field(default_factory=dict)  # 修复时注入的环境变量 (镜像等)
    soft: bool = False                    # 缺了只降级不致命 (不计入"未就绪"的硬缺口)


@dataclass
class Result:
    check: Check
    status: str   # ok | missing | skip | na
    detail: str


# ── 探测实现 ─────────────────────────────────────────────────────────────────
def probe_py_version() -> tuple[bool, str]:
    v = sys.version_info
    return v >= (3, 10), f"{v.major}.{v.minor}.{v.micro} at {PY}"


# ── 插件 venv (依赖的真正归宿) ────────────────────────────────────────────────
# 依赖由 mcp/bootstrap.py 管理: venv 在插件目录内, 首次调用自动创建。
# 体检必须探测 venv, 而不是跑 doctor 的解释器 —— 两者经常不是同一个。
BOOTSTRAP = PLUGIN_ROOT / "mcp" / "bootstrap.py"
VENV_DIR = PLUGIN_ROOT / ".venv"
VENV_PY = VENV_DIR / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _bootstrap_ensure_cmd() -> str:
    py = f'"{PY}"' if " " in PY else PY
    return f"{py} \"{BOOTSTRAP}\" --ensure"


def _requirements_stamp() -> str | None:
    """与 mcp/bootstrap.py 的 requirements_stamp 同算法: sha256 前 16 位。"""
    import hashlib

    try:
        req = REQUIREMENTS.read_text(encoding="utf-8") if REQUIREMENTS.is_file() else ""
    except OSError:
        return None
    payload = "\x00".join(
        [req, *("scenedetect>=0.6",),
         f"py{sys.version_info.major}.{sys.version_info.minor}"],
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def venv_state() -> tuple[str, str]:
    """→ (state, detail). state: missing | healthy | stale | broken。
    healthy = 解释器在 + 戳与 requirements 匹配; stale = 戳不匹配(增量重装即可);
    broken = 解释器在但戳缺失(半装)。missing 时首次调用会自动建, 不是缺口。"""
    if not VENV_PY.is_file():
        return "missing", f"未创建 (首次调用 MCP/CLI 时自动构建): {VENV_DIR}"
    stamp = VENV_DIR / ".vak-stamp"
    if not stamp.is_file():
        return "broken", f"{VENV_DIR} 不完整 (缺戳文件); --ensure 可修复"
    expect = _requirements_stamp()
    if expect is None:
        return "broken", "读 requirements.txt 失败"
    if stamp.read_text(encoding="utf-8").strip() != expect:
        return "stale", "requirements 已变化, 下次调用自动增量重装 (或 --ensure 立即)"
    return "healthy", f"{VENV_DIR}"


def probe_venv() -> tuple[bool, str]:
    state, detail = venv_state()
    # missing/healthy/stale 都算 ok (stale 会在下次调用自动增量装); broken 需要修
    return state != "broken", f"{state}: {detail}"


def _venv_probe(code: str) -> tuple[bool, str]:
    """在插件 venv 解释器里跑一段探测代码 (单行, 分号分隔), 返回 (ok, detail)。
    venv missing 时不报缺 (自动构建); broken 才是缺口。"""
    state, detail = venv_state()
    if state == "missing":
        return True, f"venv 未创建 (首次调用自动构建)"
    if state == "broken":
        return False, f"venv 损坏: {detail}"
    import subprocess as _sp

    r = _sp.run([str(VENV_PY), "-c", code], capture_output=True, text=True,
                timeout=60, stdin=_sp.DEVNULL)
    out = r.stdout.strip()
    err = (r.stderr or r.stdout or "").strip()
    if r.returncode != 0:
        return False, (err.splitlines()[-1][:160] if err else "探测失败")
    return (not out.startswith("MISSING")), out or "已装"


def probe_pip() -> tuple[bool, str]:
    """pip must be present AND not locked by PEP 668 (externally-managed),
    otherwise every --fix install silently fails. System Pythons on Debian 12+,
    Ubuntu 23.04+, Homebrew, and slim images are the common offenders."""
    if importlib.util.find_spec("pip") is None:
        return False, "未安装 (python -m pip 不可用)"
    import sysconfig

    candidates = [Path(sys.prefix) / "EXTERNALLY-MANAGED"]
    try:
        stdlib = Path(sysconfig.get_path("stdlib"))
        candidates.append(stdlib / "EXTERNALLY-MANAGED")
        candidates.append(stdlib.parent / "EXTERNALLY-MANAGED")
    except Exception:  # noqa: BLE001
        pass
    for c in candidates:
        try:
            if c.is_file():
                return False, ("外部托管环境 (PEP 668 externally-managed): pip 拒绝直接装包 — "
                               "用 venv/pipx, 或加 --break-system-packages")
        except OSError:
            continue
    return True, f"可用 {_dist_version('pip') or ''}".strip()


def probe_module(mod: str) -> Callable[[], tuple[bool, str]]:
    """在插件 venv 里探测 (依赖的真正归宿); venv missing 视为待自动构建。"""
    def _p() -> tuple[bool, str]:
        return _venv_probe(
            "import importlib.util as u; "
            f"print('MISSING' if u.find_spec({mod!r}) is None else '已装')"
        )
    return _p


def _dist_version(name: str) -> str | None:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:
        return None


def probe_mcp() -> tuple[bool, str]:
    """mcp 1.x —— server_common.py 用的是 1.x 的 Server.list_tools/call_tool 装饰器
    API, 2.x 改了这套接口, "装了但是大版本不对"会让所有工具消失而不是报错。
    在插件 venv 里探测 (依赖的真正归宿); venv missing 视为待自动构建。"""
    return _venv_probe(
        "from importlib.metadata import version; "
        "v = version('mcp'); "
        "m = int(v.split('.')[0]); "
        "print(v if m == 1 else f'{v} [!] 需要 1.x (2.x 改了 Server API)')"
    )


def probe_cv2_conflict() -> tuple[bool, str]:
    """opencv-python(GUI) 与 opencv-python-headless 混装 —— 两个包提供同一个 cv2
    目录, pip 不认为它们冲突, 装第二个会覆盖第一个的文件, 结果是 import cv2 成功
    但某些符号缺失/崩在 libGL 上。requirements.txt 只要 headless。
    在插件 venv 里探测; venv missing 视为待自动构建。"""
    return _venv_probe(
        "import importlib.metadata as m; "
        "names = {d.metadata['Name'] for d in m.distributions()}; "
        "print('MISSING: venv 里同时装了 opencv-python (GUI 版) 和 headless' "
        "if ('opencv-python' in names and 'opencv-python-headless' in names) else "
        "('仅 headless, 干净' if 'opencv-python-headless' in names else "
        "'cv2 未装 (见 py-cv2 项)'))"
    )


def probe_bins(*bins: str) -> Callable[[], tuple[bool, str]]:
    def _p() -> tuple[bool, str]:
        found = {b: shutil.which(b) for b in bins}
        miss = [b for b, p in found.items() if not p]
        if miss:
            return False, "PATH 上找不到: " + " ".join(miss)
        return True, "; ".join(f"{b}={p}" for b, p in found.items())
    return _p


def _ffmpeg_list(kind: str) -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        return ""
    try:
        p = subprocess.run([exe, "-hide_banner", f"-{kind}"],
                           capture_output=True, text=True, timeout=60)
    except Exception:
        return ""
    return p.stdout + p.stderr


def probe_ffmpeg_encoders() -> tuple[bool, str]:
    """要的不是"有 ffmpeg", 而是"这个 ffmpeg 带我们用的编码器"。conda / 静态包的
    ffmpeg 经常裁掉 libx264 或 libmp3lame, 表现是渲染跑到最后一步才炸。"""
    text = _ffmpeg_list("encoders")
    if not text:
        return False, "ffmpeg -encoders 无输出"
    want = {"libx264": "预览/成片视频编码", "aac": "成片音频编码",
            "libmp3lame": "音频中间件 mp3"}
    miss = [f"{k}({v})" for k, v in want.items() if k not in text]
    if miss:
        return False, "缺编码器: " + ", ".join(miss)
    return True, "libx264 / aac / libmp3lame 齐"


def probe_ffmpeg_filters() -> tuple[bool, str]:
    """字幕烧录 = libass(subtitles/ass 滤镜), 标题卡/角标 = libfreetype(drawtext),
    段间溶解 = xfade。这三个缺任何一个都不会在启动时报错, 只会在渲染那一步失败。"""
    text = _ffmpeg_list("filters")
    if not text:
        return False, "ffmpeg -filters 无输出"
    want = {"subtitles": "字幕烧录 (libass)", "ass": "ASS 字幕烧录 (libass)",
            "drawtext": "标题卡/文字轨 (libfreetype)", "xfade": "段间溶解"}
    miss = [f"{k}({v})" for k, v in want.items() if f" {k} " not in text]
    if miss:
        return False, "缺滤镜: " + ", ".join(miss)
    return True, "subtitles / ass / drawtext / xfade 齐"


def _load_kit_module(name: str):
    """import 插件自己的 mcp/ve_tools/<name> —— 体检必须问代码本身, 不许在这里
    另写一份"字体/凭据怎么找"的逻辑 (那必然和渲染时的实际行为漂移)。"""
    sys.path.insert(0, str(PLUGIN_ROOT / "mcp"))
    from importlib import import_module

    return import_module(f"ve_tools.{name}")


def probe_font() -> tuple[bool, str]:
    """本机有没有能真写中文的字体 —— 而且要是**代码那条解析路径**找得到的。

    这里不查"某个目录下有没有某个文件", 而是直接调 ve_tools.fonts, 因为字幕
    分行是拿真字体量出来的, 体检和渲染必须看同一个结果。cmap 真覆盖也要查:
    fc-match 对着没装的中文族会返回 DejaVu, 烧出来是豆腐块而 ffmpeg 退出码 0。
    在插件 venv 解释器里跑 (fontTools 在 venv 里; doctor 自己的解释器没有)。
    """
    state, detail = venv_state()
    if state == "missing":
        return True, "venv 未创建, 字体体检推迟到首次调用后 (自动构建)"
    if state == "broken":
        return False, f"venv 损坏, 无法体检字体: {detail}"
    import subprocess as _sp

    code = (
        "import sys, json; sys.path.insert(0, r'" + str(PLUGIN_ROOT / "mcp").replace("'", "\\'") + "'); "
        "from ve_tools import fonts; "
        "print(json.dumps(fonts.diagnose(), ensure_ascii=False))"
    )
    r = _sp.run([str(VENV_PY), "-c", code], capture_output=True, text=True,
                timeout=60, stdin=_sp.DEVNULL)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        return False, f"ve_tools.fonts 探测失败: {err.splitlines()[-1][:160] if err else '未知错误'}"
    import json as _json

    try:
        d = _json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return False, f"ve_tools.fonts 返回不可解析: {r.stdout.strip()[:120]}"
    if not d["regular"]:
        dirs = ", ".join(d["existing_dirs"]) or "(以上目录都不存在)"
        return False, f"未找到任何中文字体; 已搜: {dirs}"
    covered = d["covers_cjk"]
    tail = (f"; family={d['ass_font_name']}"
            + ("" if covered is not False else " [!] 该字体 cmap 无中文, 会烧成豆腐块"))
    if covered is False:
        return False, f"{d['regular']}{tail}"
    if covered is None:
        # venv 里 fontTools 缺失 → 覆盖度未知, 但文件在, 不当缺口 (py-fontTools 那项会报)
        return True, f"{d['regular']}{tail} (覆盖度未验证: 缺 fontTools)"
    return True, f"{d['regular']}{tail}"


def probe_asr() -> tuple[bool, str]:
    """本地转录 CLI (Moss Transcribe WebUI)。走 _load_kit_module("local_asr") 直接调
    运行时的解析逻辑 (含 .env 层), 体检与实际调用必须看同一个结果; 无服务时 CLI
    自启, 不预检服务端口。"""
    try:
        local_asr = _load_kit_module("local_asr")
    except Exception as e:
        return False, f"local_asr 模块不可用: {type(e).__name__}: {e}"
    status = local_asr.local_asr_status()
    if not status.get("available"):
        return False, "; ".join(status.get("reasons") or ["未找到"])
    return True, f"{status.get('command')} (服务 {status.get('host')}:{status.get('port')}, 无服务时自动拉起)"


def probe_disk() -> tuple[bool, str]:
    target = PROJECT_DIR if PROJECT_DIR.exists() else PROJECT_DIR.parent
    try:
        free_gb = shutil.disk_usage(target).free / 2**30
    except OSError as e:
        return False, f"{target} 无法统计: {e}"
    return (free_gb >= MIN_FREE_GB,
            f"{target} 剩余 {free_gb:.1f}G" + ("" if free_gb >= MIN_FREE_GB else f" (<{MIN_FREE_GB}G)"))


# ── 检查清单 (顺序即展示顺序) ────────────────────────────────────────────────
PY_MODULES = [
    ("cv2", "opencv-python-headless>=4.8.0,<5.0.0", "video_ingest 抽帧 / 局部复看 / QC 扫描"),
    ("numpy", "numpy>=1.24.0", "抽帧索引与帧差计算"),
    ("PIL", "pillow>=10.0.0", "contact sheet 拼图 (模型能看到的那张图)"),
    ("jieba", "jieba>=0.42.1", "subtitle_build 的中文分词/词性 — 缺则字幕断行不可用"),
    ("fontTools", "fonttools>=4.40.0", "字体 cmap 校验 (烧字幕前拦下豆腐块)"),
]

CHECKS: list[Check] = [
    Check("py", "python3 ≥ 3.10", "MCP server 用了 3.10+ 运行时语法 (X | Y 类型别名)",
          probe_py_version,
          need="Python 3.10 或更高 (bootstrap 用它创建插件 .venv, 语法约束仍适用)",
          fix=["# 换一个 3.10+ 解释器, 例如 conda create -n va python=3.12"],
          cheap=True),
    Check("venv", "插件 venv (依赖归宿)", "python 依赖装在插件目录内 .venv — 首次调用 MCP/CLI 时自动构建, 不碰全局环境",
          probe_venv,
          need="missing/healthy/stale 均为正常态 (自动构建/自动增量); broken 才需要修",
          fix=[f"# 手动 (预) 构建 / 修复: {_bootstrap_ensure_cmd()}",
               f"# venv 位置: {VENV_DIR} (随插件安装/更新/卸载走; VE_VENV_DIR 可改)"],
          cheap=True, needs="py"),
    Check("py-pip", "python 包管理: pip (非 PEP 668 外部托管)",
          "bootstrap 建 venv/装依赖都靠它 — pip 缺失或被 externally-managed 拦住时, venv 构建会失败",
          probe_pip,
          need="pip 可用, 且不是 externally-managed 环境 (系统 Python 常被 PEP 668 锁)",
          fix=["# pip 本身缺失时先确保: python -m ensurepip --upgrade",
               "# PEP 668 只锁系统解释器; bootstrap 建的 venv 不受影响, 通常无需处理"],
          cheap=True, needs="py"),
    Check("py-mcp", "python 包: mcp (1.x)", "MCP server 本体 — 缺则所有剪辑工具都不存在 (装进插件 venv)",
          probe_mcp,
          need="pip 包 mcp>=1.0.0,<2.0.0 (装进插件 .venv; 缺失时首跑 bootstrap 会自动装)",
          fix=[f"# 自动安装 (bootstrap 在插件 venv 内装齐全部依赖): {_bootstrap_ensure_cmd()}",
               "# 换镜像: 设 VE_PIP_INDEX 后重跑"],
          cheap=True, auto=True, slow=True, needs="venv"),
    *[Check(f"py-{mod}", f"python 包: {mod}", why, probe_module(mod),
            need=f"pip 包 {pkg} (装进插件 .venv; 由 bootstrap 统一管理)",
            fix=[f"# 统一修复入口 (在插件 venv 内装齐全部依赖): {_bootstrap_ensure_cmd()}"],
            cheap=True, auto=True, slow=True, needs="venv")
      for mod, pkg, why in PY_MODULES],
    Check("py-scenedetect", "python 包: scenedetect", "`detect_shots` 的镜头边界检测 (shots)",
          probe_module("scenedetect"),
          need="pip 包 scenedetect>=0.6, **必须 --no-deps 装** + 它的三个纯 python 依赖 (bootstrap 已内置此逻辑)",
          fix=[f"# 统一修复入口 (bootstrap 处理 --no-deps 与三个纯依赖): {_bootstrap_ensure_cmd()}"],
          cheap=True, auto=True, slow=True, needs="venv"),
    Check("cv2-clean", "cv2 无 GUI/headless 混装", "混装会让 cv2 半坏: import 成功但抽帧崩在 libGL (在插件 venv 里检查)",
          probe_cv2_conflict,
          need="插件 venv 里只装 opencv-python-headless 一个 (不要同时装 opencv-python)",
          # venv 是插件私有的, 修它不动用户全局环境
          fix=[f"# 重建干净 venv (推荐, 一并修复依赖): {_bootstrap_ensure_cmd()}",
               "# 或手动: 删除插件目录下 .venv 后重跑上面命令"],
          cheap=True, needs="py-cv2"),
    Check("ffmpeg", "ffmpeg + ffprobe", "剪切 / 渲染 / 抽帧 / 时长探测 —— 缺了整条流水线都起不来",
          probe_bins("ffmpeg", "ffprobe"),
          need="ffmpeg (含 ffprobe), 且在 PATH 上 —— 版本不挑, 4.3 起都行 (xfade 要 4.3+)",
          # by_os 的 any 只在"无本平台条目"时回落, 三平台都有条目就会吞掉 conda 行 ——
          # 但 conda 是跨平台最干净的国内源路径 (尤其 Windows, winget 没镜像), 必须显示。
          fix=by_os(
                  macos=["# brew 的 bottle 从 GitHub 下, 国内常慢/失败; 先切清华镜像再装:",
                         f"export HOMEBREW_BOTTLE_DOMAIN={HOMEBREW_BOTTLE}",
                         "brew install ffmpeg"],
                  windows=["# winget 没有镜像概念, ffmpeg 静态包来自 gyan.dev, 国内可能慢;",
                           "winget install --id Gyan.FFmpeg -e",
                           "# 或 choco install ffmpeg / scoop install ffmpeg",
                           "# 装完新开一个终端, 否则 PATH 不刷新"],
                  linux=["sudo apt-get install -y ffmpeg   # apt 源换清华/USTC 镜像更快 (改 sources.list, 系统级改动)"],
              ) + ["# 想走国内镜像 / 无 root / 不动系统包管理器 → 任何平台都用 conda:",
                   f"#   conda install -c {CONDA_FORGE_TSINGHUA} ffmpeg   # 清华镜像"],
          cheap=True),
    Check("ffmpeg-encoders", "ffmpeg 带 libx264/aac/libmp3lame", "渲染最后一步的编码; 缺了前面白跑",
          probe_ffmpeg_encoders,
          need="ffmpeg 编译时带 libx264、aac、libmp3lame (`ffmpeg -encoders` 能看到)",
          fix=by_os(
                  macos=[f"export HOMEBREW_BOTTLE_DOMAIN={HOMEBREW_BOTTLE}",
                         "brew reinstall ffmpeg   # brew 的 ffmpeg 是全功能构建"],
                  windows=["# gyan.dev 的 full 构建带全套; 精简版/自编译版才会缺",
                           "winget install --id Gyan.FFmpeg -e"],
                  linux=["sudo apt-get install -y ffmpeg   # 发行版包一般带全套; 缺的多是自编译/静态包"],
              ) + [f"# 换成 conda-forge 的全功能构建 (跨平台, 走清华镜像):",
                   f"#   conda install -c {CONDA_FORGE_TSINGHUA} ffmpeg",
                   "# 机器上有多个 ffmpeg 时先确认 PATH 上是哪个: 上面探测行里有路径"],
          cheap=True, needs="ffmpeg"),
    Check("ffmpeg-filters", "ffmpeg 带 libass/libfreetype", "字幕烧录 + 标题卡/角标 + 段间溶解",
          probe_ffmpeg_filters,
          need="ffmpeg 编译时带 libass (subtitles/ass 滤镜) 和 libfreetype (drawtext)",
          fix=by_os(
                  macos=[f"export HOMEBREW_BOTTLE_DOMAIN={HOMEBREW_BOTTLE}",
                         "brew reinstall ffmpeg"],
                  windows=["winget install --id Gyan.FFmpeg -e   # full 构建带 libass"],
                  linux=["sudo apt-get install -y ffmpeg   # 若是自编译: 重新 configure 加 --enable-libass --enable-libfreetype"],
              ) + [f"# conda 路线 (跨平台, 清华镜像): conda install -c {CONDA_FORGE_TSINGHUA} ffmpeg",
                   "# 注意: 部分极简/静态构建就是不带 libass, 换构建比补参数省事"],
          cheap=True, needs="ffmpeg"),
    Check("font-cjk", "中文字体 (真能写中文的)", "字幕烧录 / 标题卡 / contact sheet 标签",
          probe_font,
          need="一个覆盖中日韩字形的字体文件; 首选 Noto Sans CJK, "
               "系统自带的雅黑/苹方/AR PL UMing 也算 (mac/Win 一般已自带)",
          fix=by_os(
                  linux=["sudo apt-get install -y fonts-noto-cjk   # Debian/Ubuntu",
                         "# 或 dnf install google-noto-sans-cjk-fonts / pacman -S noto-fonts-cjk"],
                  macos=["# 系统自带苹方(PingFang.ttc), 正常不会缺; 真缺了再装 Noto:",
                         f"export HOMEBREW_BOTTLE_DOMAIN={HOMEBREW_BOTTLE}",
                         "brew install --cask font-noto-sans-cjk-sc"],
                  windows=["# 系统自带微软雅黑(msyh.ttc), 正常不会缺;",
                           "# 真缺了从 https://github.com/notofonts/noto-cjk 下 NotoSansSC 放进字体目录"],
              ) + ["# 字体装在别处(或想指定用哪个)时, 把目录塞给 VE_FONT_DIRS 即可:",
                   f"#   {'set' if OS == 'windows' else 'export'} VE_FONT_DIRS=<字体目录>"
                   f"   (多个目录用 {os.pathsep!r} 分隔)",
                   "# 无 root 也可以: 下 NotoSansSC-400.ttf/-700.ttf 到任意目录再设 VE_FONT_DIRS"]),
    # 本地转录: Moss Transcribe WebUI 的 CLI 在 PATH 上即可用, 无服务时 CLI 自启。
    # 只查 CLI 在不在, 不预连服务端口 (服务启动/模型加载是分钟级, 不该挡体检)。
    Check("asr-cli", "本地转录 CLI (transcribe)", "speech_transcribe — 缺则没有转录, 语音类任务全瘸",
          probe_asr,
          need="Moss Transcribe WebUI 部署好且 cli/ 目录在 PATH 上 (或 VE_TRANSCRIBE_CMD 指向 transcribe)",
          fix=["# 安装 Moss Transcribe WebUI 后, 把其 cli/ 目录加入 PATH:",
               *by_os(windows=["#   setx PATH \"%PATH%;C:\\AI\\transcribe_cpp_webui\\cli\"  (或系统设置里加, 重启终端生效)"],
                      any=["#   export PATH=\"$PATH:/path/to/transcribe_cpp_webui/cli\"  (写进 ~/.bashrc)"]),
               "# 或在 .env 里显式指定可执行文件:",
               f"#   {'set' if OS == 'windows' else 'export'} VE_TRANSCRIBE_CMD=C:/AI/transcribe_cpp_webui/cli/transcribe.bat",
               "# 服务地址可配 (默认 127.0.0.1:8390): VE_TRANSCRIBE_HOST / VE_TRANSCRIBE_PORT"],
          soft=True),
    Check("disk", f"项目盘可用空间 ≥ {MIN_FREE_GB}G", "预览/成片 mp4 + 抽帧图落在项目目录",
          probe_disk,
          need=f"{PROJECT_DIR} 所在盘 ≥ {MIN_FREE_GB}G 空闲 (长视频/长素材要多留)",
          fix=["# 清 out/ 与 .video_agent/ 下的旧产物, 或换一个盘上的工作目录"],
          cheap=True, soft=True),
]

MARK = {"ok": "[+]", "missing": "[!]", "skip": "[~]", "na": "[-]"}


def run_checks(cheap_only: bool = False, only: set[str] | None = None) -> list[Result]:
    results: list[Result] = []
    failed: set[str] = set()
    for c in CHECKS:
        if cheap_only and not c.cheap:
            continue
        if only is not None and c.id not in only:
            continue
        if c.platforms and OS not in c.platforms:
            results.append(Result(c, "na", f"本平台 ({OS}) 不适用, 无需处理"))
            continue
        if c.needs and c.needs in failed:
            results.append(Result(c, "skip", f"前置 {c.needs} 未通过, 跳过"))
            failed.add(c.id)          # 依赖链上的后续项一并跳过
            continue
        try:
            ok, detail = c.probe()
        except Exception as e:         # 探测本身炸了也是环境问题, 不许静默
            ok, detail = False, f"探测异常 {type(e).__name__}: {e}"
        if not ok:
            failed.add(c.id)
        results.append(Result(c, "ok" if ok else "missing", detail))
    return results


def render(results: list[Result]) -> str:
    lines = [f"[env] video-agent-kit 环境体检 (平台 {OS}; 解释器 {PY})"]
    for r in results:
        lines.append(f"  {MARK[r.status]} {r.check.label:30} {r.detail}")
    # na = 本平台不适用, 不是缺口 —— 不计进未就绪
    bad = [r for r in results if r.status in ("missing", "skip")]
    if not bad:
        lines.append("[env] 全部就绪 — 无需安装。")
        return "\n".join(lines)

    hard = [r for r in bad if not r.check.soft]
    lines.append(f"[env] {len(bad)}/{len(results)} 项未就绪"
                 + (f" (其中 {len(hard)} 项是硬缺口)" if len(hard) != len(bad) else "")
                 + "。下面每项先说**要什么**, 再给本平台的示例命令 "
                   "(用你惯用的装法也行, 要求才是硬的):")
    for i, r in enumerate(bad, 1):
        if r.status == "skip":
            # 没真探测过 → 不摆修复命令, 免得照着装一个可能压根不缺的东西
            lines.append(f"  {i}. {r.check.label} [未探测: 前置 {r.check.needs} 没过] — "
                         f"{r.check.why}; 修好前置后重跑本脚本")
            continue
        tag = "自动可装" if r.check.auto else "要你自己装"
        if r.check.soft:
            tag += ", 只降级不致命"
        if r.check.slow:
            tag += ", 耗时数分钟 → Bash timeout 拉到 600000"
        lines.append(f"  {i}. {r.check.label} [{tag}] — 缺了挂: {r.check.why}")
        if r.check.need:
            lines.append(f"       需要: {r.check.need}")
        for cmd in r.check.fix:
            lines.append(f"       {cmd}")
    if any(r.check.auto for r in bad if r.status == "missing"):
        lines.append(f"[env] 自动项可一把过: python3 {Path(__file__).name} --fix "
                     "(自动项统一走 bootstrap, 依赖装进插件 .venv, 不碰全局环境)")
    return "\n".join(lines)


def do_fix(results: list[Result]) -> list[Result]:
    """跑 auto 项的修复命令, 然后只复测这些项。非 auto 项一律不碰。"""
    todo = [r for r in results if r.status == "missing" and r.check.auto]
    if not todo:
        print("[env] 没有可自动装的项 (剩下的要你自己装, 见上面的『需要』行)。")
        return results
    fixed: set[str] = set()
    for r in todo:
        env = {**os.environ, **r.check.env} if r.check.env else None
        for cmd in r.check.fix:
            if cmd.lstrip().startswith("#"):
                continue
            print(f"[env] $ {cmd}", flush=True)
            p = subprocess.run(cmd, shell=True, timeout=1800, env=env)
            if p.returncode != 0:
                print(f"[env] [!] 失败 (退出码 {p.returncode}): {r.check.label} — "
                      "先看上面的报错; 常见根因: externally-managed (PEP 668, 用 venv/"
                      "--break-system-packages)、镜像源/代理、或 pip 权限不足",
                      flush=True)
                break
        else:
            fixed.add(r.check.id)
    if not fixed:
        return results
    recheck = {res.check.id: res for res in run_checks(only=fixed)}
    return [recheck.get(r.check.id, r) for r in results]


def main() -> int:
    # On Windows the locale encoding (cp936 etc.) cannot encode some symbols;
    # force UTF-8 with errors="replace" so a diagnostic never dies as a
    # UnicodeEncodeError.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="video-agent-kit 环境体检")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--cheap", action="store_true", help="只跑毫秒级项 (无网络)")
    ap.add_argument("--fix", action="store_true", help="自动安装 auto 项后复测")
    args = ap.parse_args()

    results = run_checks(cheap_only=args.cheap)
    if args.fix:
        results = do_fix(results)

    payload = {"plugin_root": str(PLUGIN_ROOT),
               "platform": OS,
               "python": PY,
               "pip_index": PIP_INDEX,
               "checks": [{"id": r.check.id, "label": r.check.label,
                           "status": r.status, "detail": r.detail,
                           "why": r.check.why, "need": r.check.need,
                           "auto": r.check.auto, "soft": r.check.soft,
                           "fix": r.check.fix if r.status == "missing" else []}
                          for r in results]}
    # na (本平台不适用) 不算缺口; skip (前置没过) 算 —— 它背后有个真缺口没修
    payload["missing"] = [c["id"] for c in payload["checks"]
                          if c["status"] in ("missing", "skip")]
    payload["blocking"] = [c["id"] for c in payload["checks"]
                           if c["status"] in ("missing", "skip") and not c["soft"]]

    # 落回执: 下一轮/别的会话不用重跑一遍全量探测就知道上次结论。
    try:
        out = PROJECT_DIR / ".video_agent"
        out.mkdir(parents=True, exist_ok=True)
        (out / "env_report.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass

    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json
          else render(results))
    return 1 if payload["blocking"] else 0


if __name__ == "__main__":
    sys.exit(main())
