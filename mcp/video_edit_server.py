#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# venv 自举必须先于任何第三方 import (mcp SDK 等); 失败时给出可读指引。
try:
    from bootstrap import ensure_venv

    ensure_venv()
except SystemExit:
    raise
except Exception as _exc:  # noqa: BLE001 — 自举失败不能是裸 traceback
    print(
        f"[ERROR] venv bootstrap failed: {_exc}\n"
        f"手动修复: python3 \"{Path(__file__).resolve().parent / 'bootstrap.py'}\" --ensure",
        file=sys.stderr,
    )
    raise SystemExit(1)

from server_common import serve
from ve_tools import (
    basic_ops,
    condense,
    diarize,
    frame_zoom,
    media,
    qc,
    render,
    shots,
    subtitle,
    subtitle_scout,
    timeline,
    video_observe,
)
from ve_tools.run_context import RunContext
from ve_tools.schemas import TOOL_SCHEMAS


def main() -> None:
    # MCP 工具跑在单 worker ThreadPoolExecutor 线程里。cv2 / numpy / PIL 的
    # C 扩展若在 worker 线程首次加载, 在多线程 asyncio 进程内会卡死数十秒到
    # 数分钟 (验收实测: worker 卡在 create_module 的 DLL 加载, 见验收报告
    # P1-B); 同样导入在主线程仅 ~77ms。启动时于主线程预加载, 消除该竞态。
    import numpy  # noqa: F401
    import PIL.Image  # noqa: F401
    import cv2  # noqa: F401

    ctx = RunContext(session_kind="mcp")
    impls = {
        "inspect_media": media.inspect_media,
        "analyze_media": media.analyze_media,
        "speech_transcribe": media.speech_transcribe,
        "transcribe": media.transcribe,
        "video_ingest": video_observe.video_ingest,
        "video_watch_segment": video_observe.video_watch_segment,
        "video_basic_operation": basic_ops.video_basic_operation,
        "subtitle_scout": subtitle_scout.subtitle_scout,
        "subtitle_build": subtitle.subtitle_build,
        "subtitle_render": subtitle.subtitle_render,
        "subtitle_qc": subtitle.subtitle_qc,
        "condense_index": condense.condense_index,
        "condense_plan": condense.condense_plan,
        "condense_render": condense.condense_render,
        "condense_qc": condense.condense_qc,
        "diarize_audit": diarize.diarize_audit,
        "diarize_relabel": diarize.diarize_relabel,
        "video_read_frames": frame_zoom.video_read_frames,
        "validate_timeline": timeline.validate_timeline,
        "render_preview": render.render_preview,
        "qc_preview": qc.qc_preview,
        "timeline_diff": timeline.timeline_diff,
        "detect_shots": shots.detect_shots,
    }
    serve("video-agent-kit-local", TOOL_SCHEMAS, impls, ctx)


if __name__ == "__main__":
    main()
