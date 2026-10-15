"""local_asr: 本地转录后端 —— Moss Transcribe WebUI (transcribe.cpp) 的 CLI。

集成契约（与远端通道不同，这里的一切都在本机）：

- 命令解析：``VE_TRANSCRIBE_CMD`` 覆盖 > PATH 上的 ``transcribe``（Windows 上是
  ``transcribe.bat``，内部优先用部署自带的 venv python，纯 stdlib 客户端）。
- CLI 自动对接运行中的 WebUI 服务（``VE_TRANSCRIBE_HOST`` / ``VE_TRANSCRIBE_PORT``，
  默认 ``127.0.0.1:8390``）；本机无服务时自动拉起并在结束后停止。插件不管理
  服务生命周期，也不需要任何凭据。
- 长音频走 CLI ``--autosplit``（分钟，``VE_TRANSCRIBE_AUTOSPLIT`` 默认 30，0 关闭）：
  服务端用 ffmpeg 按真实时长精确切分，每段一个任务，``params.seg_offset_ms`` 把
  时间戳修正为全片绝对时间。说话人编号跨段不互通，多段结果沿用既有
  ``p{段}_{说话人}`` 前缀约定（与原云端分块路径一致，下游文档已认知该语义）。
- CLI 把 job JSON 写在**输入文件旁**，因此输入先硬链（跨盘回退复制）进 work_dir，
  绝不往用户源目录写任何东西；产物 JSON 也随之落在 work_dir。
- 退出码语义：0 成功；1 用法/输入错误（不重试）；2 服务错误（可重试）；3 任务
  失败（可重试，由调用方按 retries 控制）。
- job JSON 口径：``status/error/detail`` + ``result.{text, language, segments[
  {text, t0_ms, t1_ms, speaker_id}], words[{text, t0_ms, t1_ms, seg_index}],
  speaker_segments}``。本模块把它映射为插件 transcript.json 契约：秒时间戳、
  ``segments[].speaker``（原始 1 起可能不连续的 speaker_id 归一成连续 1..N）、
  ``words[].speaker`` 经 ``seg_index`` 回连所属 segment 的说话人。
  **时间戳粒度是段级**：Moss 系模型不支持词级时间戳（引擎 status 12），``words``
  恒为空数组；下游按既有降级路径处理（karaoke 回退字符权重估算、condense 的
  drop_fillers 退化为整单元删除）。词映射逻辑保留 —— 未来模型放开词级时无需改动。

模型语言自判（引擎对 language 参数实测无效），``language`` 入参只被记录进
payload，不影响调用。
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from .ffproc import run_proc
from .result import ToolResult
from .run_context import clean_env

PROVIDER = "local_asr"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8390
DEFAULT_AUTOSPLIT_MIN = 30
# 单文件上传上限与服务端一致（CLI 同值硬拒）；超限应在提交前给出可读错误。
MAX_INPUT_BYTES = 32 * 1024 ** 3
CLI_TIMEOUT_MIN_SECONDS = 600.0


def transcribe_command() -> str | None:
    """Resolve the local transcribe CLI: VE_TRANSCRIBE_CMD override > PATH lookup."""
    configured = (clean_env("VE_TRANSCRIBE_CMD") or "").strip()
    if configured:
        # 允许给完整路径（含空格），也允许只给命令名交给 PATH 解析。
        candidate = Path(configured)
        if candidate.is_file():
            return str(candidate)
        found = shutil.which(configured)
        if found:
            return found
        return None
    return shutil.which("transcribe")


def local_asr_status() -> dict[str, Any]:
    """{available, reasons, ...}，与原远端通道的 status 形状一致，供能力探测复用。"""
    reasons: list[str] = []
    cmd = transcribe_command()
    if not cmd:
        reasons.append(
            "local transcribe CLI not found: install Moss Transcribe WebUI and put its "
            "cli/ folder on PATH, or set VE_TRANSCRIBE_CMD to the transcribe executable"
        )
    return {
        "available": not reasons,
        "reasons": reasons,
        "provider": PROVIDER,
        "command": cmd,
        "host": (clean_env("VE_TRANSCRIBE_HOST") or DEFAULT_HOST).strip() or DEFAULT_HOST,
        "port": _port_value(),
    }


def _port_value() -> int:
    raw = (clean_env("VE_TRANSCRIBE_PORT") or "").strip()
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PORT
    return port if 1 <= port <= 65535 else DEFAULT_PORT


def _autosplit_value(override: Any) -> int:
    raw = override if override is not None else clean_env("VE_TRANSCRIBE_AUTOSPLIT")
    if raw is None or str(raw).strip() == "":
        return DEFAULT_AUTOSPLIT_MIN
    try:
        minutes = int(float(raw))
    except (TypeError, ValueError):
        return DEFAULT_AUTOSPLIT_MIN
    return max(0, min(180, minutes))


def _stage_input(input_path: Path, work_dir: Path) -> Path:
    """把输入放进 work_dir（CLI 会往输入旁写 job JSON，不能污染用户源目录）。

    硬链零拷贝；跨盘 os.link 失败回退完整复制。文件名保留 stem+后缀，同
    work_dir 本来就按源路径哈希隔离，不会互撞。
    """
    staged = work_dir / input_path.name
    if staged.exists() and staged.stat().st_size == input_path.stat().st_size:
        return staged
    staged.unlink(missing_ok=True)
    try:
        os.link(input_path, staged)
    except OSError:
        shutil.copy2(input_path, staged)
    return staged


def local_asr_transcribe_payload(
    input_path: Path,
    work_dir: Path,
    *,
    diarize: bool = True,
    autosplit_min: Any = None,
    backend: str | None = None,
    timeout_seconds: float = 3600.0,
    started: float = 0.0,
) -> tuple[dict[str, Any], Path] | ToolResult:
    """跑一次本地转录，返回 (transcript payload, 首个 job JSON 路径) 或 ToolResult。"""
    status = local_asr_status()
    if not status["available"]:
        return ToolResult(
            text="[ERROR] local_asr is not available: " + "; ".join(status["reasons"]),
            data={"recoverable": False, "provider": PROVIDER, "status": status},
        )
    if input_path.stat().st_size > MAX_INPUT_BYTES:
        return ToolResult(
            text=(
                f"[ERROR] local_asr input too large ({input_path.stat().st_size / 2**30:.1f} GiB > "
                f"{MAX_INPUT_BYTES // 2**30} GiB service upload limit)"
            ),
            data={"provider": PROVIDER, "recoverable": False},
        )

    work_dir.mkdir(parents=True, exist_ok=True)
    staged = _stage_input(input_path, work_dir)
    autosplit = _autosplit_value(autosplit_min)
    cmd = [
        status["command"],
        str(staged),
        "--output", "json",
        "--timestamp", "segments",
        "--diarize", "on" if diarize else "off",
        "--host", str(status["host"]),
        "--port", str(status["port"]),
    ]
    if autosplit > 0:
        cmd += ["--autosplit", str(autosplit)]
    resolved_backend = (backend or clean_env("VE_TRANSCRIBE_BACKEND") or "").strip()
    if resolved_backend:
        cmd += ["--backend", resolved_backend]

    started = started or time.time()
    try:
        proc = run_proc(
            cmd, capture_output=True, text=True,
            timeout=max(CLI_TIMEOUT_MIN_SECONDS, float(timeout_seconds)),
        )
    except Exception as exc:  # noqa: BLE001 — 启动失败/超时统一按可重试处理
        return ToolResult(
            text=f"[ERROR] local_asr CLI failed to run: {exc}",
            data={"provider": PROVIDER, "recoverable": True, "cmd": _safe_cmd(cmd)},
        )

    if proc.returncode != 0:
        # CLI 退出码契约: 1 = 用法/输入错误(不可重试), 2 = 服务错误, 3 = 任务失败(都可重试)
        usage_error = proc.returncode == 1
        detail = (proc.stderr or proc.stdout or "").strip()[-1500:]
        return ToolResult(
            text=f"[ERROR] local_asr transcription failed (exit {proc.returncode}): {detail}",
            data={
                "provider": PROVIDER,
                "recoverable": not usage_error,
                "param_error": usage_error,
                "exit_code": proc.returncode,
                "cmd": _safe_cmd(cmd),
            },
        )

    job_paths = _collect_job_jsons(staged)
    if not job_paths:
        return ToolResult(
            text=(
                "[ERROR] local_asr CLI reported success but no job JSON was written to "
                f"{work_dir}"
            ),
            data={
                "provider": PROVIDER,
                "recoverable": True,
                "cmd": _safe_cmd(cmd),
                "stdout_tail": (proc.stdout or "").strip()[-800:],
            },
        )
    payload = _merge_job_jsons(
        job_paths,
        source_media=input_path,
        elapsed_seconds=round(time.time() - started, 3),
    )
    if isinstance(payload, ToolResult):
        return payload
    return payload, job_paths[0]


def _safe_cmd(cmd: list[str]) -> str:
    return " ".join(cmd[:2]) + " …" if len(cmd) > 2 else " ".join(cmd)


def _collect_job_jsons(staged: Path) -> list[Path]:
    """单段产物是 <stem>.json；autosplit 多段是 <stem>.1ofN.json …（按段序命名）。"""
    single = staged.with_suffix(".json")
    if single.is_file():
        return [single]
    parts = sorted(
        p for p in staged.parent.glob(f"{staged.stem}.*of*.json")
        if p.is_file()
    )
    return parts


def _speaker_map(result: dict[str, Any]) -> dict[int, int]:
    """原始 speaker_id（1 起、可能不连续）→ 连续 1..N，与 WebUI 导出口径一致。"""
    ids: set[int] = set()
    for seg in result.get("segments") or []:
        sid = seg.get("speaker_id", -1)
        if isinstance(sid, int) and sid >= 0:
            ids.add(sid)
    for seg in result.get("speaker_segments") or []:
        sid = seg.get("speaker_id", -1)
        if isinstance(sid, int) and sid >= 0:
            ids.add(sid)
    return {sid: i for i, sid in enumerate(sorted(ids), 1)}


def _ms_to_seconds(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    if parsed < 0:
        return 0.0
    return round(parsed / 1000.0, 3)


def _merge_job_jsons(
    job_paths: list[Path],
    *,
    source_media: Path,
    elapsed_seconds: float,
) -> dict[str, Any] | ToolResult:
    """一份或多份（autosplit 分段）job JSON → transcript.json 契约。

    分段时间戳是段内相对值，加 ``params.seg_offset_ms`` 修正为全片绝对时间
    （服务端按每段真实时长累加，零漂移）。说话人归一在段内进行；多段时给
    ``p{段}_{说话人}`` 前缀并附 speaker_note —— 与原云端分块路径同一约定。
    """
    segments: list[dict[str, Any]] = []
    words: list[dict[str, Any]] = []
    text_parts: list[str] = []
    languages: list[str] = []
    n_parts = len(job_paths)
    audio_ms_total: float | None = None
    errors: list[str] = []

    for idx, path in enumerate(job_paths):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return ToolResult(
                text=f"[ERROR] local_asr job JSON unreadable ({path.name}): {exc}",
                data={"provider": PROVIDER, "recoverable": True},
            )
        if job.get("status") != "done":
            errors.append(f"{path.name}: {job.get('status')} ({job.get('error') or job.get('detail') or ''})")
            continue
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        offset_ms = float((job.get("params") or {}).get("seg_offset_ms") or 0)
        audio_ms = job.get("audio_ms")
        if isinstance(audio_ms, (int, float)) and audio_ms is not None:
            audio_ms_total = offset_ms + float(audio_ms)
        smap = _speaker_map(result)

        part_segments = result.get("segments") or []
        # 与原云端分块路径同约定: 单段无前缀; 多段所有段都带 p{段}_ 前缀
        prefix = "" if n_parts == 1 else f"p{idx}_"
        speaker_for_index: list[str] = []
        seg_rows: list[dict[str, Any]] = []
        for seg in part_segments:
            if not isinstance(seg, dict):
                speaker_for_index.append("")
                continue
            text = str(seg.get("text") or "").strip()
            raw_sid = seg.get("speaker_id", -1)
            speaker = (
                f"{prefix}{smap[raw_sid]}" if isinstance(raw_sid, int) and raw_sid in smap else ""
            )
            speaker_for_index.append(speaker)
            if not text:
                continue
            seg_rows.append({
                "speaker": speaker,
                "text": text,
                "start": _ms_to_seconds(float(seg.get("t0_ms") or 0) + offset_ms),
                "end": _ms_to_seconds(float(seg.get("t1_ms") or 0) + offset_ms),
            })
        segments.extend(seg_rows)

        # words[].seg_index 指向同一 result.segments 数组 → 说话人可回连
        for w in result.get("words") or []:
            if not isinstance(w, dict):
                continue
            wtext = str(w.get("text") or "").strip()
            if not wtext:
                continue
            seg_index = w.get("seg_index")
            speaker = (
                speaker_for_index[seg_index]
                if isinstance(seg_index, int) and 0 <= seg_index < len(speaker_for_index)
                else ""
            )
            words.append({
                "text": wtext,
                "start": _ms_to_seconds(float(w.get("t0_ms") or 0) + offset_ms),
                "end": _ms_to_seconds(float(w.get("t1_ms") or 0) + offset_ms),
                "speaker": speaker or None,
            })

        full_text = str(result.get("text") or "").strip()
        text_parts.append(full_text or "\n".join(row["text"] for row in seg_rows))
        lang = str(result.get("language") or "").strip()
        if lang:
            languages.append(lang)

    if errors and not segments:
        return ToolResult(
            text="[ERROR] local_asr transcription failed: " + "; ".join(errors),
            data={"provider": PROVIDER, "recoverable": True, "part_errors": errors},
        )

    text = "\n".join(part for part in text_parts if part).strip()
    speaker_labels = any(seg["speaker"] for seg in segments)
    payload: dict[str, Any] = {
        "provider": PROVIDER,
        "language": languages[0] if languages else "",
        "speaker_labels": speaker_labels,
        "source_media": str(source_media),
        "source_kind": "local_transcribe_cli",
        "text": text,
        "words": words,
        "segments": segments,
        "elapsed_seconds": elapsed_seconds,
        "time_unit": "seconds",
    }
    if audio_ms_total is not None:
        payload["audio_duration_seconds"] = round(audio_ms_total / 1000.0, 3)
    if n_parts > 1:
        payload["chunks"] = n_parts
        payload["speaker_note"] = (
            "speaker labels are per-split clusters (p{part}_{speaker}); "
            "the same person may carry different labels across splits"
        )
    if errors:
        payload["part_errors"] = errors
    if not segments and not text:
        payload["silent_audio"] = True
        payload["note"] = (
            "local_asr produced no speech segments (silent/no-speech audio); "
            "empty transcript is valid"
        )
    return payload
