from __future__ import annotations

import hashlib
import json
import math
import shutil
from .ffproc import run_proc
import time
from pathlib import Path
from typing import Any

from .result import ToolResult
from .run_context import RunContext, clean_env
from .local_asr import local_asr_transcribe_payload
from .transcript import read_transcript_text

ASR_TIMEOUT_SECONDS = 3600.0
ASR_DEFAULT_RETRIES = 1
ASR_MAX_RETRIES = 10
ASR_RETRY_BACKOFF_SECONDS = 10.0


def run_json(cmd: list[str], timeout: int = 60) -> dict:
    proc = run_proc(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"command failed: {cmd}")
    return json.loads(proc.stdout)


def coerce_asr_retry_count(value: object) -> int | ToolResult:
    if value is None:
        return ASR_DEFAULT_RETRIES
    if isinstance(value, bool):
        return ToolResult(text="[ERROR] retries must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return ToolResult(text="[ERROR] retries must be an integer")
    try:
        raw = float(value)
    except (TypeError, ValueError):
        raw = float(parsed)
    if not math.isfinite(raw) or raw != parsed:
        return ToolResult(text="[ERROR] retries must be an integer")
    if parsed < 0 or parsed > ASR_MAX_RETRIES:
        return ToolResult(text=f"[ERROR] retries must be in [0, {ASR_MAX_RETRIES}]")
    return parsed


def coerce_nonnegative_number(value: object, field: str, default: float) -> float | ToolResult:
    if value is None:
        return default
    if isinstance(value, bool):
        return ToolResult(text=f"[ERROR] {field} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return ToolResult(text=f"[ERROR] {field} must be numeric")
    if not math.isfinite(parsed) or parsed < 0:
        return ToolResult(text=f"[ERROR] {field} must be a finite number >= 0")
    return parsed


def run_asr_with_retries(call: Any, *, retries: int, retry_backoff_seconds: float) -> tuple[Any, int]:
    result: Any = ToolResult(text="[ERROR] ASR failed")
    attempts: list[dict[str, Any]] = []
    for attempt in range(retries + 1):
        result = call()
        if not isinstance(result, ToolResult):
            return result, attempt
        retryable = asr_error_retryable(result)
        attempts.append({
            "attempt": attempt + 1,
            "error": result.text,
            "retryable": retryable,
        })
        if not retryable or attempt >= retries:
            break
        if retry_backoff_seconds > 0:
            time.sleep(float(retry_backoff_seconds) * (2 ** attempt))
    if isinstance(result, ToolResult):
        result.data = {
            **(result.data or {}),
            "attempts": attempts,
            "retry_attempts": max(0, len(attempts) - 1),
            "max_retries": retries,
        }
    return result, max(0, len(attempts) - 1)


def asr_error_retryable(result: ToolResult) -> bool:
    """local_asr 后端的错误可重试性。

    local_asr 在每个错误 ToolResult 上都带结构化标记: param_error=True (用法/
    输入错误) 与 recoverable=False 不可重试; recoverable=True (CLI exit 2 服务
    错误 / exit 3 任务失败 / 产物不可读) 可重试。文本启发式只作最后兜底。
    """
    data = result.data or {}
    if data.get("param_error"):
        return False
    if data.get("recoverable") is False:
        return False
    if data.get("recoverable") is True:
        return True
    text = result.text.lower()
    non_retryable = [
        "input_path is required",
        "file not found",
        "not set",
        "not available",
        "ffmpeg not found",
        "audio too large",
        "must be",
        "unsupported",
    ]
    if any(marker in text for marker in non_retryable):
        return False
    if data.get("retryable") is True:
        return True
    return message_retryable(text)


def message_retryable(message: object) -> bool:
    text = str(message or "").lower()
    retry_markers = [
        "429",
        "too many",
        "rate limit",
        "ratelimit",
        "concurrency",
        "concurrent",
        "busy",
        "throttl",
        "temporar",
        "timeout",
        "timed out",
        "try again",
        "server error",
        "service unavailable",
        "gateway",
        "connection",
        "reset",
    ]
    return any(marker in text for marker in retry_markers)


def inspect_media(args: dict, ctx: RunContext) -> ToolResult:
    if not args.get("input_path"):
        return ToolResult(text="[ERROR] input_path is required")
    input_path = ctx.resolve(args["input_path"])
    if not input_path.is_file():
        return ToolResult(text=f"[ERROR] File not found: {input_path}")
    if not shutil.which("ffprobe"):
        return ToolResult(text="[ERROR] ffprobe not found on PATH")

    started = time.time()
    try:
        data = run_json([
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", str(input_path)
        ])
    except Exception as exc:
        return ToolResult(text=f"[ERROR] ffprobe failed: {exc}")
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    duration_seconds = safe_float(fmt.get("duration"))
    if duration_seconds is None:
        stream_durations = [safe_float(s.get("duration")) for s in streams if isinstance(s, dict)]
        stream_durations = [value for value in stream_durations if value is not None]
        duration_seconds = max(stream_durations) if stream_durations else None
    out = {
        "input_path": str(input_path),
        "duration_seconds": duration_seconds,
        "size_bytes": safe_int(fmt.get("size")) or 0,
        "format_name": fmt.get("format_name"),
        "bit_rate": safe_int(fmt.get("bit_rate")),
        "streams": streams,
        "video_streams": [s for s in streams if s.get("codec_type") == "video"],
        "audio_streams": [s for s in streams if s.get("codec_type") == "audio"],
        "elapsed_seconds": round(time.time() - started, 3),
    }
    out["summary"] = media_summary(out)
    out["technical_risks"] = media_technical_risks(out)
    output_json = args.get("output_json")
    artifacts = []
    if output_json:
        p = ctx.resolve(output_json)
    else:
        # 多源任务的默认路径: out/media.json 是"单一主源"约定; 已存在且记录的是
        # 另一个输入时改落 out/media/<stem>.json, 避免逐源探测互相覆盖 (4 个源
        # 跑完只剩最后一个的实测教训)。显式传 output_json 永远尊重调用方。
        p = ctx.ensure_output_dir() / "media.json"
        if p.is_file():
            try:
                previous = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                previous = {}
            prev_input = str(previous.get("input_path") or "")
            if prev_input and prev_input != str(input_path):
                p = ctx.ensure_output_dir() / "media" / f"{input_path.stem}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    artifacts.append(str(p))
    return ToolResult(
        text=f"Inspected media: {ctx.virtualize(input_path)} -> {ctx.virtualize(p)}",
        data=out,
        artifacts=artifacts,
    )


def analyze_media(args: dict, ctx: RunContext) -> ToolResult:
    """Lightweight deterministic source analysis for planning, not a semantic judge."""
    if not args.get("input_path"):
        return ToolResult(text="[ERROR] input_path is required")
    input_path = ctx.resolve(args["input_path"])
    if not input_path.is_file():
        return ToolResult(text=f"[ERROR] File not found: {input_path}")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        return ToolResult(text="[ERROR] ffmpeg/ffprobe not found on PATH")
    scene_threshold = coerce_threshold(args.get("scene_threshold"), default=0.30)
    if isinstance(scene_threshold, ToolResult):
        return scene_threshold
    started = time.time()
    probe_sidecar = ctx.ensure_work_dir("analysis") / f"{input_path.stem}_media.json"
    probe = inspect_media({"input_path": str(input_path), "output_json": str(probe_sidecar)}, ctx)
    if probe.text.startswith("[ERROR]"):
        return ToolResult(text=f"[ERROR] inspect failed during analyze_media: {probe.text}", data=probe.data)
    duration = safe_float((probe.data.get("summary") or {}).get("duration_seconds"))
    scenes, scene_log = scan_scene_changes(input_path, scene_threshold)
    black_ranges, black_log = scan_ranges(input_path, "blackdetect=d=0.2:pix_th=0.10", "black")
    silence_ranges, silence_log = scan_ranges(input_path, "silencedetect=n=-35dB:d=0.5", "silence")
    candidate_segments = segments_from_boundaries(duration, scenes)
    report = {
        "input_path": str(input_path),
        "duration_seconds": duration,
        "scene_threshold": scene_threshold,
        "scene_change_times": scenes,
        "candidate_segments": candidate_segments,
        "black_ranges": black_ranges,
        "silence_ranges": silence_ranges,
        "notes": [
            "candidate_segments are deterministic scene-boundary hints, not final editorial choices",
            "use video_ingest/video_watch_segment to visually verify any candidate before timeline decisions",
        ],
        "logs": {
            "scene_tail": scene_log[-2000:],
            "black_tail": black_log[-2000:],
            "silence_tail": silence_log[-2000:],
        },
        "elapsed_seconds": round(time.time() - started, 3),
    }
    output_json = ctx.resolve(args.get("output_json") or "out/media_analysis.json")
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # The full report (scene_change_times / candidate_segments / black & silence
    # ranges) is written to disk above. Inlining it into data too would dump
    # hundreds of KB / six figures of tokens for a long film into one tool
    # result. Return only a bounded summary here; Read the artifact for detail.
    summary = {
        "input_path": report["input_path"],
        "duration_seconds": duration,
        "scene_threshold": scene_threshold,
        "scene_change_count": len(scenes),
        "candidate_segment_count": len(candidate_segments),
        "black_range_count": len(black_ranges),
        "silence_range_count": len(silence_ranges),
        "notes": report["notes"],
        "elapsed_seconds": report["elapsed_seconds"],
    }
    return ToolResult(
        text=(
            f"Media analysis written: {ctx.virtualize(output_json)} "
            f"({len(scenes)} scene changes, {len(candidate_segments)} candidate segments). "
            f"Read {ctx.virtualize(output_json)} for the full boundary list before timeline decisions."
        ),
        data=summary,
        artifacts=[str(output_json)],
    )


def safe_float(value) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except Exception:
        return None
    if not math.isfinite(parsed):
        return None
    return round(parsed, 6)


def safe_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def coerce_threshold(value: object, *, default: float) -> float | ToolResult:
    if value is None:
        return default
    if isinstance(value, bool):
        return ToolResult(text="[ERROR] scene_threshold must be numeric")
    try:
        parsed = float(value)
    except Exception:
        return ToolResult(text="[ERROR] scene_threshold must be numeric")
    if not math.isfinite(parsed) or parsed <= 0 or parsed >= 1:
        return ToolResult(text="[ERROR] scene_threshold must be in (0, 1)")
    return parsed


def scan_scene_changes(path: Path, threshold: float) -> tuple[list[float], str]:
    expr = f"select='gt(scene,{threshold:.6f})',showinfo"
    cmd = ["ffmpeg", "-v", "info", "-i", str(path), "-vf", expr, "-f", "null", "-"]
    try:
        proc = run_proc(cmd, capture_output=True, text=True, timeout=900)
    except Exception as exc:
        return [], str(exc)
    output = (proc.stderr or "") + (proc.stdout or "")
    times = []
    for line in output.splitlines():
        marker = "pts_time:"
        if marker not in line:
            continue
        tail = line.split(marker, 1)[1].split()[0]
        value = safe_float(tail)
        if value is not None:
            times.append(value)
    return sorted(set(round(t, 3) for t in times if t >= 0)), output


def scan_ranges(path: Path, filter_expr: str, kind: str) -> tuple[list[dict], str]:
    filter_arg = "-af" if kind == "silence" else "-vf"
    cmd = ["ffmpeg", "-v", "info", "-i", str(path), filter_arg, filter_expr, "-f", "null", "-"]
    try:
        proc = run_proc(cmd, capture_output=True, text=True, timeout=900)
    except Exception as exc:
        return [], str(exc)
    output = (proc.stderr or "") + (proc.stdout or "")
    starts: list[float] = []
    ranges = []
    start_marker = f"{kind}_start:"
    end_marker = f"{kind}_end:"
    duration_marker = f"{kind}_duration:"
    for line in output.splitlines():
        if start_marker in line:
            value = safe_float(line.split(start_marker, 1)[1].split()[0])
            if value is not None:
                starts.append(value)
        if end_marker in line:
            end = safe_float(line.split(end_marker, 1)[1].split()[0])
            duration = safe_float(line.split(duration_marker, 1)[1].split()[0]) if duration_marker in line else None
            start = starts.pop(0) if starts else ((end - duration) if end is not None and duration is not None else None)
            if start is not None and end is not None:
                ranges.append({"start": round(start, 3), "end": round(end, 3), "duration": round(end - start, 3)})
    return ranges, output


def segments_from_boundaries(duration: float | None, boundaries: list[float]) -> list[dict]:
    if duration is None or duration <= 0:
        return []
    points = [0.0] + [t for t in boundaries if 0.1 < t < duration - 0.1] + [duration]
    points = sorted(set(round(t, 3) for t in points))
    segments = []
    for idx, (start, end) in enumerate(zip(points, points[1:])):
        if end - start < 0.3:
            continue
        segments.append({
            "index": idx,
            "start": start,
            "end": end,
            "duration": round(end - start, 3),
            "source": "scene_boundary",
        })
    return segments


def media_summary(probe: dict) -> dict:
    video = first_stream(probe.get("video_streams"))
    audio = first_stream(probe.get("audio_streams"))
    return {
        "duration_seconds": probe.get("duration_seconds"),
        "has_video": bool(video),
        "has_audio": bool(audio),
        "width": safe_int(video.get("width")) if video else None,
        "height": safe_int(video.get("height")) if video else None,
        "fps": stream_fps(video) if video else None,
        "video_codec": video.get("codec_name") if video else None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "audio_channels": safe_int(audio.get("channels")) if audio else None,
        "audio_sample_rate": safe_int(audio.get("sample_rate")) if audio else None,
        "pix_fmt": video.get("pix_fmt") if video else None,
        "color_space": video.get("color_space") if video else None,
        "color_transfer": video.get("color_transfer") if video else None,
        "rotation": stream_rotation(video) if video else None,
    }


def media_technical_risks(probe: dict) -> list[dict]:
    risks = []
    summary = probe.get("summary") or {}
    if not summary.get("has_video") and not summary.get("has_audio"):
        risks.append({"severity": "error", "message": "no video or audio stream detected"})
    elif not summary.get("has_video"):
        risks.append({"severity": "warning", "message": "media has no video stream"})
    if summary.get("has_video") and not summary.get("has_audio"):
        risks.append({"severity": "warning", "message": "video has no audio stream"})
    width = summary.get("width") or 0
    height = summary.get("height") or 0
    if width and height and (width % 2 or height % 2):
        risks.append({"severity": "warning", "message": "odd video dimensions may require padding before H.264 render"})
    if width and height and width * height >= 3840 * 2160:
        risks.append({"severity": "info", "message": "4K-or-larger source may be expensive to sample/render"})
    video = first_stream(probe.get("video_streams"))
    if video:
        avg = stream_fps(video, "avg_frame_rate")
        nominal = stream_fps(video, "r_frame_rate")
        if avg and nominal and abs(avg - nominal) > 0.5:
            risks.append({
                "severity": "warning",
                "message": "possible variable-frame-rate source",
                "evidence": f"avg_frame_rate={avg:.3f}, r_frame_rate={nominal:.3f}",
            })
        if stream_rotation(video):
            risks.append({"severity": "info", "message": f"rotation metadata present: {stream_rotation(video)}"})
        transfer = str(video.get("color_transfer") or "").lower()
        if transfer in {"smpte2084", "arib-std-b67"}:
            risks.append({"severity": "warning", "message": f"HDR transfer detected ({transfer}); SDR preview may need tonemapping"})
    return risks


def first_stream(streams: object) -> dict:
    if isinstance(streams, list) and streams and isinstance(streams[0], dict):
        return streams[0]
    return {}


def stream_fps(stream: dict, key: str | None = None) -> float | None:
    keys = [key] if key else ["avg_frame_rate", "r_frame_rate"]
    for item in keys:
        value = stream.get(item)
        if not value or value == "0/0":
            continue
        try:
            if "/" in str(value):
                num, den = str(value).split("/", 1)
                den_f = float(den)
                if den_f == 0:
                    continue
                fps = float(num) / den_f
            else:
                fps = float(value)
        except Exception:
            continue
        if math.isfinite(fps) and fps > 0:
            return round(fps, 6)
    return None


def stream_rotation(stream: dict) -> str | None:
    tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
    if tags.get("rotate"):
        return str(tags["rotate"])
    side_data = stream.get("side_data_list")
    if isinstance(side_data, list):
        for item in side_data:
            if isinstance(item, dict) and item.get("rotation") not in (None, 0, "0"):
                return str(item["rotation"])
    return None


PROVIDER_NAME = "local_asr"
ASR_PROVIDERS = {"auto", "local_asr", "local"}


def transcribe(args: dict, ctx: RunContext) -> ToolResult:
    """Compatibility alias. ASR is exposed as speech_transcribe."""
    return speech_transcribe(args, ctx)


def speech_transcribe(args: dict, ctx: RunContext) -> ToolResult:
    """Transcribe a media file with the local transcribe CLI (Moss WebUI backend)."""
    if not args.get("input_path"):
        return ToolResult(text="[ERROR] input_path is required")
    input_path = ctx.resolve(args["input_path"])
    if not input_path.is_file():
        return ToolResult(text=f"[ERROR] File not found: {input_path}")
    output_path = ctx.resolve(args.get("output_json") or "out/transcript.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    provider_arg = str(args.get("provider") or clean_env("VE_ASR_PROVIDER") or "auto").strip().lower()
    if provider_arg not in ASR_PROVIDERS:
        return ToolResult(
            text=f"[ERROR] provider must be one of: {', '.join(sorted(ASR_PROVIDERS))}",
            data={"supported_providers": sorted(ASR_PROVIDERS)},
        )

    retries = coerce_asr_retry_count(args.get("retries", args.get("retry_count")))
    if isinstance(retries, ToolResult):
        return retries
    retry_backoff_seconds = coerce_nonnegative_number(
        args.get("retry_backoff_seconds"), "retry_backoff_seconds", ASR_RETRY_BACKOFF_SECONDS,
    )
    if isinstance(retry_backoff_seconds, ToolResult):
        return retry_backoff_seconds
    timeout_seconds = coerce_positive_number(args.get("timeout_seconds"), "timeout_seconds", ASR_TIMEOUT_SECONDS)
    if isinstance(timeout_seconds, ToolResult):
        return timeout_seconds

    diarize = args.get("diarize")
    if diarize is None:
        diarize = True
    if not isinstance(diarize, bool):
        return ToolResult(text="[ERROR] diarize must be a boolean")
    # 本地模型自判语言; 入参仅记录请求意图, 不影响识别
    requested_language = str(args.get("language") or "").strip()

    started = time.time()
    # work_dir 按源路径哈希隔离: 不同目录的同名源视频不共用转写中间产物
    if args.get("work_dir"):
        work_dir = ctx.resolve(args["work_dir"])
    else:
        path_digest = hashlib.sha256(str(input_path.resolve()).encode("utf-8")).hexdigest()[:8]
        work_dir = ctx.resolve(f".video_agent/asr/{input_path.stem}_{path_digest}")
    work_dir.mkdir(parents=True, exist_ok=True)

    result, retry_attempts = run_asr_with_retries(
        lambda: local_asr_transcribe_payload(
            input_path,
            work_dir,
            diarize=diarize,
            autosplit_min=args.get("autosplit_min"),
            backend=args.get("backend"),
            timeout_seconds=float(timeout_seconds),
            started=started,
        ),
        retries=retries,
        retry_backoff_seconds=float(retry_backoff_seconds),
    )
    if isinstance(result, ToolResult):
        return result
    transcript_payload, job_json_path = result
    transcript_payload["retry_attempts"] = retry_attempts
    transcript_payload["max_retries"] = retries
    if requested_language:
        transcript_payload["requested_language"] = requested_language

    output_path.write_text(
        json.dumps(transcript_payload, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    transcript_text = read_transcript_text(output_path)
    transcript_fingerprint = ctx.file_fingerprint(output_path)
    ctx.remember_transcript_for_video(
        input_path, output_path, transcript_text,
        transcript_fingerprint=transcript_fingerprint,
    )
    if (
        ctx.active_video_path is not None
        and ctx.active_video_key is not None
        and ctx.active_video_key == ctx.video_memory_key(input_path)
    ):
        ctx.active_transcript_path = output_path
        ctx.active_transcript_text = transcript_text
        ctx.active_transcript_fingerprint = transcript_fingerprint

    data = {
        "provider": PROVIDER_NAME,
        "tool": "speech_transcribe",
        "output_json": str(output_path),
        "source_media": str(input_path),
        "job_json": str(job_json_path),
        "language": transcript_payload.get("language") or "",
        **({"requested_language": requested_language} if requested_language else {}),
        # 本地 ASR (Moss 模型) 无词级时间戳, words 恒为空 —— word_count=0 是预期, 不是转写出错
        "word_count": len(transcript_payload.get("words", [])),
        "word_level_supported": bool(transcript_payload.get("words")),
        "segment_count": len(transcript_payload.get("segments", [])),
        "n_speakers": len({
            seg.get("speaker") for seg in transcript_payload.get("segments", [])
            if seg.get("speaker")
        }) or None,
        "retry_attempts": retry_attempts,
        "max_retries": retries,
        "elapsed_seconds": round(time.time() - started, 3),
    }
    if transcript_payload.get("silent_audio"):
        data["silent_audio"] = True
    if transcript_payload.get("chunks"):
        data["chunks"] = transcript_payload["chunks"]
    return ToolResult(
        text=f"{PROVIDER_NAME} transcript written: {ctx.virtualize(output_path)}",
        data=data,
        artifacts=[str(output_path), str(job_json_path)],
    )



def coerce_positive_number(value: object, field: str, default: float) -> float | ToolResult:
    if value is None:
        return default
    if isinstance(value, bool):
        return ToolResult(text=f"[ERROR] {field} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return ToolResult(text=f"[ERROR] {field} must be numeric")
    if not math.isfinite(parsed) or parsed <= 0:
        return ToolResult(text=f"[ERROR] {field} must be a finite number > 0")
    return parsed
