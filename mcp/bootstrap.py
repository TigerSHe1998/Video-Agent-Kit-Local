"""venv 自举：插件入口在 import 任何第三方包之前先落到插件目录内的 venv。

venv 放在 ``${CLAUDE_PLUGIN_ROOT}/.venv``，跟着插件的安装/更新/卸载生命周期走：
- 安装 → 首次调用时自动创建并装依赖；
- 更新（宿主整体替换插件目录）→ venv 随之清空，入口检测到缺失自动重建
  （更新后依赖重装一次是预期行为）；
- 卸载 → venv 随插件目录一起删除，不留任何用户看不见的残留。

requirements.txt 哈希变化时自动增量安装。VE_VENV_DIR 可显式指定其他位置。

自举协议（入口脚本最前面调用 :func:`ensure_venv`）：
1. ``VE_VENV_ACTIVE=1`` 表示已在 venv 解释器内（由 re-exec 设置；用户预设它
   可跳过 re-exec 直接用当前解释器，但依赖不齐时仍会触发安装与重定向——
   它不是"用裸环境硬跑"的开关）；
2. venv 缺失 → 用当前解释器 ``python -m venv`` 创建；存在但 requirements 哈希
   过期 → 只做增量 ``pip install -r``；
3. 重入 venv 解释器重跑入口脚本：POSIX 上 ``os.execv`` 原地覆盖进程；Windows
   的 execv 是"spawn 新进程 + 父进程立即退出"的模拟（退出码不透传、含空格的
   参数被静默拆散、路径含空格时整个重入崩掉），因此 Windows 改用子进程等待
   等价重入并透传退出码——父进程全程存活，stdio 由子进程继承，MCP stdio
   语义不变；
4. venv 内 import 失败时最多自愈一次（删戳重装再重入），防循环。

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

# 官方 PyPI; 特殊网络 (企业代理等) 可用 VE_PIP_INDEX 覆盖, 不内置第三方镜像。
DEFAULT_PIP_INDEX = "https://pypi.org/simple"
STAMP_NAME = ".vak-stamp"
BUILD_LOCK = ".vak-build.lock"
BUILD_WAIT_TIMEOUT_S = 1800.0
# 锁内记录的进程死了 → 立即判定过期; 记录读不出/判不了 → mtime 兜底。
# 活着的持锁进程不回收 (构建慢不等于死了, 抢过来会变成两个 pip 写同一 venv)。
STALE_LOCK_SECONDS = 1800.0
# 锁内记录的是持锁进程的**创建时刻** (不是抢锁时刻 —— 进程可能跑了很久才来
# 抢锁), 与查询到的创建时刻同源比对, 容差只吸收时钟换算误差; 也顺带防 PID
# 复用: 同号新进程的创建时刻对不上 → 记录里的那个已死。
LOCK_START_TOLERANCE_S = 60.0
# 抢锁失败且持锁方没建成 (崩溃/安装失败/锁过期回收) 时重新抢锁接管的次数上限。
MAX_TAKEOVERS = 3

ACTIVE_ENV = "VE_VENV_ACTIVE"
REPAIRS_ENV = "VE_VENV_REPAIRS"
MAX_REPAIRS = 1
VENV_DIR_ENV = "VE_VENV_DIR"

# 自举后必须可 import 的模块表 —— requirements.txt 的直接依赖 + scenedetect 的
# 三个纯 py 依赖 (其 CLI 入口要 click; --no-deps 安装不带它们, 需显式列出)。
REQUIRED_MODULES = (
    "mcp", "cv2", "numpy", "PIL", "jieba", "fontTools", "scenedetect",
    "click", "platformdirs", "tqdm",
)
STALE_LOCK_SECONDS = 1800.0


def venv_dir() -> Path:
    configured = (os.environ.get(VENV_DIR_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return PLUGIN_ROOT / ".venv"


def venv_python(vdir: Path) -> Path:
    return vdir / "Scripts" / "python.exe"


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
    # stdin=DEVNULL: 子进程绝不能抢 MCP stdio 管道里的字节 (同 ffproc 的教训)。
    # stdout 重定向到 stderr: pip 的进度输出绝不能写进 MCP 的 JSON-RPC 管道
    # (宿主把本进程 stdout 当协议流), stderr 是安全通道。
    return subprocess.run(cmd, stdin=subprocess.DEVNULL,
                          stdout=sys.stderr, stderr=sys.stderr)


def _stamp_ok(vdir: Path) -> bool:
    stamp = _stamp_path(vdir)
    try:
        return stamp.read_text(encoding="utf-8").strip() == requirements_stamp()
    except OSError:
        return False


def _read_lock_record(lock: Path) -> tuple[int, float] | None:
    """锁文件里记的 (pid, 进程创建时刻 unix 秒); 旧版空锁/损坏锁 → None (走 mtime 兜底)。"""
    try:
        parts = lock.read_text(encoding="utf-8").split()
        return int(parts[0]), float(parts[1])
    except (OSError, ValueError, IndexError):
        return None


_K32 = None


def _win_kernel32():
    """(kernel32, FILETIME) 原型就绪, 模块级缓存; 非 Windows → None。"""
    global _K32
    if _K32 is None:
        if os.name != "nt":
            return None
        import ctypes
        import ctypes.wintypes as wt

        class FILETIME(ctypes.Structure):
            _fields_ = [("low", wt.DWORD), ("high", wt.DWORD)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wt.HANDLE
        k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k32.GetProcessTimes.restype = wt.BOOL
        k32.GetProcessTimes.argtypes = [wt.HANDLE] + [
            ctypes.POINTER(FILETIME)] * 4
        k32.CloseHandle.argtypes = [wt.HANDLE]
        _K32 = (k32, FILETIME)
    return _K32


def _own_creation_unix() -> float | None:
    """本进程创建时刻 (unix 秒); 非 Windows / 查询失败 → None (锁里记 0, 判定降级)。"""
    win = _win_kernel32()
    if win is None:
        return None
    k32, FILETIME = win
    import ctypes

    try:
        created, exited, _k, _u = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        # GetCurrentProcess 伪句柄, 无需 CloseHandle
        if not k32.GetProcessTimes(k32.GetCurrentProcess(), ctypes.byref(created),
                                   ctypes.byref(exited), ctypes.byref(_k), ctypes.byref(_u)):
            return None
        return ((created.high << 32) | created.low) / 1e7 - 11644473600.0
    except Exception:
        return None


def _process_alive(pid: int, created: float) -> bool | None:
    """持锁进程是否还活着 (Windows)。True/False = 判定成功; None = 判不了
    (非 Windows / 权限拒绝 / ctypes 异常), 调用方回退 mtime 兜底。

    created > 0 时比对进程创建时间防 PID 复用; 记录里没有 (旧版/降级写 0)
    就只看 pid 存在与否。
    """
    win = _win_kernel32()
    if win is None:
        return None
    k32, FILETIME = win
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    ERROR_INVALID_PARAMETER = 87
    try:
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            # 87 = PID 不存在 (死了); 其他错误 (拒绝访问等) 判不了
            if ctypes.get_last_error() != ERROR_INVALID_PARAMETER:
                return None
            return False
        try:
            c, exited, _k, _u = FILETIME(), FILETIME(), FILETIME(), FILETIME()
            if not k32.GetProcessTimes(handle, ctypes.byref(c), ctypes.byref(exited),
                                       ctypes.byref(_k), ctypes.byref(_u)):
                return True  # 活着但查不到时间: 保守按活
            if exited.low or exited.high:
                return False  # 句柄还在但进程已退出
            if created <= 0:
                return True  # 记录里无创建时刻: 只能信 pid 存活
            unix = ((c.high << 32) | c.low) / 1e7 - 11644473600.0
            return abs(unix - created) <= LOCK_START_TOLERANCE_S
        finally:
            k32.CloseHandle(handle)
    except Exception:
        return None


def _lock_is_stale(lock: Path) -> bool:
    """锁能否回收: 记录的进程已死 → True (立即); 判不了 → mtime 超龄兜底。"""
    rec = _read_lock_record(lock)
    if rec is not None:
        alive = _process_alive(*rec)
        if alive is not None:
            return not alive
    try:
        return time.time() - lock.stat().st_mtime > STALE_LOCK_SECONDS
    except OSError:
        return False


def _reclaim_stale_lock(lock: Path) -> None:
    rec = _read_lock_record(lock)
    why = f"holder pid {rec[0]} not running" if rec else f"lock untouched for >{STALE_LOCK_SECONDS:.0f}s"
    try:
        lock.unlink(missing_ok=True)
        print(f"[bootstrap] stale build lock reclaimed ({why}) — taking over",
              file=sys.stderr, flush=True)
    except OSError:
        pass


def install_or_create(vdir: Path) -> None:
    """创建 venv（缺失时）并增量安装依赖。`bootstrap.py --ensure` 与冷启动共用此入口。

    抢不到锁 → 等持锁方: stamp 就绪即返回; 持锁方没建成 (崩溃 / 安装失败 /
    锁过期被回收) → 回到循环头重新抢锁接管安装 (上限 MAX_TAKEOVERS 次)。
    不允许"锁被回收了但谁都没装"就空手返回 —— 那是假成功。
    """
    vdir.mkdir(parents=True, exist_ok=True)
    lock = vdir / BUILD_LOCK
    takeovers = 0
    while True:
        locked = False
        try:
            try:
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    # 记录创建时刻而非抢锁时刻: 进程可能跑了很久才来抢锁
                    created = _own_creation_unix()
                    os.write(fd, f"{os.getpid()} {created if created is not None else 0.0:.3f}\n"
                                 .encode("ascii"))
                finally:
                    os.close(fd)
                locked = True
            except FileExistsError:
                _wait_for_build(vdir)
                if _stamp_ok(vdir):
                    return
                takeovers += 1
                if takeovers > MAX_TAKEOVERS:
                    raise RuntimeError(
                        f"并发构建连续 {MAX_TAKEOVERS} 次未建成 (持锁方反复失败); "
                        f"排查网络/磁盘后重跑: python \"{PLUGIN_ROOT / 'mcp' / 'bootstrap.py'}\" --ensure")
                continue
            except OSError:
                pass  # 无法加锁 (权限等): 单进程继续, 最坏是重复 install (pip 幂等)

            py = venv_python(vdir)
            if not py.is_file():
                r = _run([sys.executable, "-m", "venv", "--clear", str(vdir)])
                if r.returncode != 0 or not py.is_file():
                    raise RuntimeError(f"创建 venv 失败 (exit {r.returncode}); 常见原因: 缺少 venv/ensurepip 组件")
                r = _run([str(py), "-m", "pip", "install", "--upgrade", "pip", "-i", _pip_index()])
                if r.returncode != 0:
                    raise RuntimeError("升级 pip 失败; 查网络后重试 (特殊网络可用 VE_PIP_INDEX 指定源)")

            if not REQUIREMENTS.is_file():
                raise RuntimeError(f"requirements.txt 不存在: {REQUIREMENTS}")
            r = _run([str(py), "-m", "pip", "install", "-i", _pip_index(), "-r", str(REQUIREMENTS)])
            if r.returncode != 0:
                raise RuntimeError(
                    f"安装 requirements.txt 失败 (exit {r.returncode}); "
                    f"查网络后重试 (当前源 {_pip_index()}; 特殊网络可用 VE_PIP_INDEX 更换)"
                )
            r = _run([str(py), "-m", "pip", "install", "-i", _pip_index(), "--no-deps", *EXTRA_SPECS])
            if r.returncode != 0:
                raise RuntimeError(f"安装 {' '.join(EXTRA_SPECS)} 失败 (exit {r.returncode})")

            _stamp_path(vdir).write_text(requirements_stamp(), encoding="utf-8")
            return
        finally:
            if locked:
                try:
                    lock.unlink(missing_ok=True)
                except OSError:
                    pass


def _wait_for_build(vdir: Path) -> None:
    """并发入口 (CLI 与 server 同时冷启动): 等持锁方建完。

    返回的三种情况 —— ① stamp 已就绪 (持锁方建完, 即使锁还没来得及删);
    ② 锁文件消失 (持锁方失败退出, stamp 没写); ③ 过期锁被本函数回收
    (持锁进程已死, 或 mtime 超龄兜底)。②③ 之后由调用方重新抢锁接管安装。
    超时抛 RuntimeError。
    """
    lock = vdir / BUILD_LOCK
    rec = _read_lock_record(lock)
    holder = f", holder pid {rec[0]}" if rec else ""
    print(f"[bootstrap] waiting for concurrent venv build{holder} (lock: {lock})",
          file=sys.stderr, flush=True)
    deadline = time.time() + BUILD_WAIT_TIMEOUT_S
    while time.time() < deadline:
        if _stamp_ok(vdir):
            return
        try:
            if not lock.exists():
                return
            if _lock_is_stale(lock):
                _reclaim_stale_lock(lock)
                return
        except OSError:
            return
        time.sleep(2.0)
    raise RuntimeError(f"等待并行 venv 构建超时 ({BUILD_WAIT_TIMEOUT_S:.0f}s): {vdir}")


def _reexec() -> None:
    """重入 venv 解释器重跑入口脚本（仅常规 ``python server.py args`` 形态）。

    ``python -c "..."`` / ``python -`` / ``python -m mod`` 形态下 sys.argv[0]
    是 "-c"/"-"/模块名, execv 无法原样重放 —— 这些场景的调用方 (import
    bootstrap 的内联/工具代码) 自己管理环境, 不做重入, 直接返回让调用
    方在当前解释器里继续 (依赖若缺, 由 ensure_venv 的 VE_VENV_ACTIVE 分支
    或调用方显式 --ensure 处理)。

    Windows 不用 ``os.execv``: 它是"spawn 新进程 + 父进程立即退出"的模拟,
    实测 (video-agent-test/report.md P1-A) 三个后果 —— ①调用方永远拿到
    bootstrap 阶段的退出码 0, 真实工具在孤儿进程里异步跑; ②execv 拼命令行
    不给含空格的参数加引号, 带空格的路径/参数被静默拆散; ③解释器或插件
    路径含空格时连可执行文件都找不到。改用子进程等价重入: subprocess 用
    list 传参 (自动加引号), 父进程等待并透传退出码, stdio 句柄由子进程
    继承 —— MCP stdio 会话语义与 POSIX execv 一致。
    """
    argv0 = sys.argv[0]
    if not argv0 or argv0.startswith("-") or not Path(argv0).is_file():
        return
    py = venv_python(venv_dir())
    if not py.is_file():
        raise RuntimeError(f"venv 解释器不存在: {py}")
    os.environ[ACTIVE_ENV] = "1"
    argv = [str(py), str(Path(argv0).resolve()), *sys.argv[1:]]
    if os.name == "nt":
        proc = subprocess.run(argv)
        raise SystemExit(proc.returncode if proc.returncode is not None else 1)
    os.execv(str(py), argv)


def _repairs() -> int:
    try:
        return int(os.environ.get(REPAIRS_ENV) or "0")
    except ValueError:
        return 0


def ensure_venv() -> None:
    """入口脚本第一行调用: 必要时建 venv 并 re-exec 进去, 幂等。"""
    if sys.version_info < (3, 10):
        raise RuntimeError(
            f"需要 Python 3.10+ (当前 {sys.version.split()[0]}); venv 由当前解释器"
            "创建, 换一个 3.10+ 的 python 再启动本插件"
        )
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
                    help="create/refresh the venv without re-exec")
    args = ap.parse_args()
    vdir = venv_dir()
    if args.ensure:
        install_or_create(vdir)
        # 兜底: install_or_create 正常返回即应已写戳; 万一没有, 绝不报成功
        if not _stamp_ok(vdir):
            print(f"[bootstrap] --ensure 返回但 venv 仍不完整 (戳校验失败): {vdir}; "
                  "请查看上方报错后重跑", file=sys.stderr)
            return 1
        print(f"venv ready: {vdir}")
        return 0
    healthy = venv_python(vdir).is_file() and _stamp_ok(vdir)
    print(f"venv_dir={vdir}")
    print(f"healthy={'yes' if healthy else 'no'}")
    print(f"python={venv_python(vdir)}")
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
