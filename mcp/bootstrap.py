"""venv 自举：插件入口在 import 任何第三方包之前先落到插件目录内的 venv。

venv 放在 ``${CLAUDE_PLUGIN_ROOT}/.venv``，跟着插件的安装/更新/卸载生命周期走：
- 安装 → 首次调用时自动创建并装依赖；
- 更新（宿主整体替换插件目录）→ venv 随之清空，入口检测到缺失自动重建
  （更新后依赖重装一次是预期行为）；
- 卸载 → venv 随插件目录一起删除，不留任何用户看不见的残留。

requirements.txt 哈希变化时自动增量安装。VE_VENV_DIR 可显式指定其他位置。

自举协议（入口脚本最前面调用 :func:`ensure_venv`）：
1. ``VE_VENV_ACTIVE=1`` 时视为已在 venv 内（也允许用户预设该变量跳过自举，
   强行使用当前解释器）；
2. venv 缺失 → 用当前解释器 ``python -m venv`` 创建；存在但 requirements 哈希
   过期 → 只做增量 ``pip install -r``；
3. ``os.execv`` 进 venv 解释器重跑入口脚本（stdio 句柄继承，MCP 不受影响）；
4. venv 内 import 失败时最多自愈一次（删戳重装再 re-exec），防循环。

本模块只用标准库 —— 它必须能在"什么都没装"的解释器里跑起来。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = PLUGIN_ROOT / "requirements.txt"

# 与 requirements.txt 尾注保持一致: scenedetect 必须独立 --no-deps 安装,
# 否则会把 GUI 版 opencv 盖到 opencv-python-headless 的 cv2 上。
EXTRA_SPECS = ("scenedetect>=0.6",)

DEFAULT_PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
STAMP_NAME = ".vak-stamp"
BUILD_LOCK = ".vak-build.lock"
BUILD_WAIT_TIMEOUT_S = 1800.0

ACTIVE_ENV = "VE_VENV_ACTIVE"
REPAIRS_ENV = "VE_VENV_REPAIRS"
MAX_REPAIRS = 1
VENV_DIR_ENV = "VE_VENV_DIR"

# 自举后必须可 import 的模块表 —— 与 requirements.txt 一一对应 + scenedetect。
REQUIRED_MODULES = ("mcp", "cv2", "numpy", "PIL", "jieba", "fontTools", "scenedetect")


def venv_dir() -> Path:
    configured = (os.environ.get(VENV_DIR_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return PLUGIN_ROOT / ".venv"


def venv_python(vdir: Path) -> Path:
    return vdir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _stamp_path(vdir: Path) -> Path:
    return vdir / STAMP_NAME


def requirements_stamp() -> str:
    req = REQUIREMENTS.read_text(encoding="utf-8") if REQUIREMENTS.is_file() else ""
    payload = "\x00".join(
        [req, *EXTRA_SPECS, f"py{sys.version_info.major}.{sys.version_info.minor}"],
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _imports_ok() -> bool:
    from importlib.util import find_spec

    return all(find_spec(name) is not None for name in REQUIRED_MODULES)


def _pip_index() -> str:
    return (os.environ.get("VE_PIP_INDEX") or DEFAULT_PIP_INDEX).strip() or DEFAULT_PIP_INDEX


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    # stdin=DEVNULL: 子进程绝不能抢 MCP stdio 管道里的字节 (同 ffproc 的教训)
    return subprocess.run(cmd, stdin=subprocess.DEVNULL)


def _stamp_ok(vdir: Path) -> bool:
    stamp = _stamp_path(vdir)
    try:
        return stamp.read_text(encoding="utf-8").strip() == requirements_stamp()
    except OSError:
        return False


def install_or_create(vdir: Path) -> None:
    """创建 venv（缺失时）并增量安装依赖。公开给 env_doctor --fix 复用。"""
    vdir.mkdir(parents=True, exist_ok=True)
    lock = vdir / BUILD_LOCK
    locked = False
    try:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            locked = True
        except FileExistsError:
            _wait_for_build(vdir)
            return
        except OSError:
            pass  # 无法加锁 (权限等): 单进程继续, 最坏是重复 install (pip 幂等)

        py = venv_python(vdir)
        if not py.is_file():
            r = _run([sys.executable, "-m", "venv", "--clear", str(vdir)])
            if r.returncode != 0 or not py.is_file():
                raise RuntimeError(f"创建 venv 失败 (exit {r.returncode}); 常见原因: 缺少 venv/ensurepip 组件")
            r = _run([str(py), "-m", "pip", "install", "--upgrade", "pip", "-i", _pip_index()])
            if r.returncode != 0:
                raise RuntimeError("升级 pip 失败; 换镜像 (VE_PIP_INDEX) 或查网络后重试")

        if not REQUIREMENTS.is_file():
            raise RuntimeError(f"requirements.txt 不存在: {REQUIREMENTS}")
        r = _run([str(py), "-m", "pip", "install", "-i", _pip_index(), "-r", str(REQUIREMENTS)])
        if r.returncode != 0:
            raise RuntimeError(
                f"安装 requirements.txt 失败 (exit {r.returncode}); "
                f"换镜像 (VE_PIP_INDEX, 当前 {_pip_index()}) 或查网络后重试"
            )
        r = _run([str(py), "-m", "pip", "install", "-i", _pip_index(), "--no-deps", *EXTRA_SPECS])
        if r.returncode != 0:
            raise RuntimeError(f"安装 {' '.join(EXTRA_SPECS)} 失败 (exit {r.returncode})")

        _stamp_path(vdir).write_text(requirements_stamp(), encoding="utf-8")
    finally:
        if locked:
            try:
                lock.unlink(missing_ok=True)
            except OSError:
                pass


def _wait_for_build(vdir: Path) -> None:
    """并发入口 (CLI 与 server 同时冷启动): 等持锁方建完。"""
    lock = vdir / BUILD_LOCK
    deadline = time.time() + BUILD_WAIT_TIMEOUT_S
    while time.time() < deadline:
        if not lock.exists() and _stamp_ok(vdir):
            return
        time.sleep(2.0)
    raise RuntimeError(f"等待并行 venv 构建超时 ({BUILD_WAIT_TIMEOUT_S:.0f}s): {vdir}")


def _reexec() -> None:
    py = venv_python(venv_dir())
    if not py.is_file():
        raise RuntimeError(f"venv 解释器不存在: {py}")
    script = Path(sys.argv[0]).resolve()
    os.environ[ACTIVE_ENV] = "1"
    os.execv(str(py), [str(py), str(script), *sys.argv[1:]])


def _repairs() -> int:
    try:
        return int(os.environ.get(REPAIRS_ENV) or "0")
    except ValueError:
        return 0


def ensure_venv() -> None:
    """入口脚本第一行调用: 必要时建 venv 并 re-exec 进去, 幂等。"""
    if os.environ.get(ACTIVE_ENV) == "1":
        # 已在目标解释器里: 只做 import 兜底 (戳文件在但依赖坏了), 最多自愈一次
        if _imports_ok():
            return
        if _repairs() < MAX_REPAIRS:
            os.environ[REPAIRS_ENV] = str(_repairs() + 1)
            install_or_create(venv_dir())
            _reexec()
        raise SystemExit(
            "[ERROR] 插件 venv 依赖不完整 (缺 "
            + ", ".join(m for m in REQUIRED_MODULES if _missing(m))
            + f"); 重跑安装: python3 \"{PLUGIN_ROOT / 'mcp' / 'bootstrap.py'}\" --ensure"
        )

    vdir = venv_dir()
    if not (venv_python(vdir).is_file() and _stamp_ok(vdir)):
        install_or_create(vdir)
    _reexec()


def _missing(name: str) -> bool:
    from importlib.util import find_spec

    return find_spec(name) is None


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="video-agent-kit-local venv bootstrap")
    ap.add_argument("--ensure", action="store_true",
                    help="create/refresh the venv without re-exec (for env_doctor --fix)")
    args = ap.parse_args()
    vdir = venv_dir()
    if args.ensure:
        install_or_create(vdir)
        print(f"venv ready: {vdir}")
        return 0
    healthy = venv_python(vdir).is_file() and _stamp_ok(vdir)
    print(f"venv_dir={vdir}")
    print(f"healthy={'yes' if healthy else 'no'}")
    print(f"python={venv_python(vdir)}")
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
