from __future__ import annotations

import os
from pathlib import Path


# (path, 全文快照, 记忆时刻的文件指纹)。指纹用于 watch 重读盘前的新鲜度校验:
# 默认输出都写 out/transcript.json, 后续 transcribe(另一视频) 会同名覆盖 —
# 只存 path 的话, 窗口级 range 过滤会重读出另一个视频的语音。
TranscriptMemory = tuple[Path | None, str, str | None]

# 媒体源参数的两等价别名: 同一概念历史上两套工具两套名 (inspect/analyze/
# speech_transcribe/detect_shots 用 input_path, 其余用 video_path), 调用方必踩。
# 现在所有工具两个名字都收; 每个工具保留各自的主名 (schema required 与默认
# 路径字段不变), 别名仅作输入。
MEDIA_INPUT_KEYS = ("video_path", "input_path")


def media_input_arg(args: dict) -> str | None:
    """媒体源参数: video_path / input_path 二选一, 都接受。"""
    for key in MEDIA_INPUT_KEYS:
        value = args.get(key)
        if value:
            return str(value)
    return None


_PLUGIN_ROOT = Path(__file__).resolve().parents[2]


def _clean_value(value: str | None) -> str | None:
    """Reject empty values and unexpanded ``${...}`` placeholders left behind
    by config layers that do not support interpolation."""
    if value is None:
        return None
    value = value.strip()
    if not value or "${" in value:
        return None
    return value


def _raw_env(*names: str) -> str | None:
    for name in names:
        value = _clean_value(os.environ.get(name))
        if value is not None:
            return value
    return None


def project_dir() -> Path:
    # Bootstrap: resolved from the process environment only — .env files are
    # themselves located relative to the project dir.
    value = _raw_env("CLAUDE_PROJECT_DIR", "VE_PROJECT_DIR")
    return Path(value).resolve() if value else Path.cwd().resolve()


def env_files() -> list[Path]:
    """Config files, lowest precedence first: plugin-root .env (kit-wide
    defaults), then project-local .env (per-project overrides)."""
    return [_PLUGIN_ROOT / ".env", project_dir() / ".env"]


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        # utf-8-sig: tolerate a BOM, which would otherwise corrupt the first key.
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            # Quoted value: strip exactly one symmetric pair; '#' inside stays.
            value = value[1:-1]
        elif value.startswith("#"):
            # The whole value is a comment.
            value = ""
        else:
            # Unquoted value: allow trailing inline comments.
            for marker in (" #", "\t#"):
                idx = value.find(marker)
                if idx != -1:
                    value = value[:idx].rstrip()
                    break
        if key:
            values[key] = value
    return values


def clean_env(*names: str) -> str | None:
    """Read the first usable config value. Precedence: process environment >
    project-local .env > plugin-root .env. Empty values and unexpanded
    ``${...}`` placeholders are skipped at every layer — a broken value in a
    higher layer falls through to the next layer instead of masking it."""
    value = _raw_env(*names)
    if value is not None:
        return value
    for path in reversed(env_files()):
        values = _parse_env_file(path)
        for name in names:
            value = _clean_value(values.get(name))
            if value is not None:
                return value
    return None





class RunContext:
    def __init__(self, session_kind: str = "cli") -> None:
        # "mcp" for the long-lived MCP server, "cli" for one-shot direct calls.
        # Long-lived MCP sessions can reuse active video/transcript state.
        #
        # out/ 与 .video_agent/ 不在构造时创建: server 随每个工作区会话启动,
        # 预防性 mkdir 会让用户"刚打开工作区、还没说话"就多出两个文件夹。
        # 目录由 ensure_output_dir/ensure_work_dir 在首次真正写产物时惰性创建。
        self.session_kind = session_kind
        self.project_dir = project_dir()
        self.output_dir = self.project_dir / "out"
        self.work_dir = self.project_dir / ".video_agent"
        self.active_video_path: Path | None = None
        self.active_video_key: str | None = None
        self.active_video_visually_ingested: bool = False
        self.active_transcript_path: Path | None = None
        self.active_transcript_text: str = ""
        self.active_transcript_fingerprint: str | None = None
        self._transcripts_by_video: dict[str, TranscriptMemory] = {}
        self._visually_ingested_video_keys: set[str] = set()

    def ensure_output_dir(self) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return self.output_dir

    def ensure_work_dir(self, *subpath: str) -> Path:
        target = self.work_dir.joinpath(*subpath) if subpath else self.work_dir
        target.mkdir(parents=True, exist_ok=True)
        return target

    def resolve(self, path: str | Path) -> Path:
        p = Path(path)
        if p.is_absolute():
            return p.resolve()
        return (self.project_dir / p).resolve()

    def virtualize(self, path: str | Path) -> str:
        p = Path(path).resolve()
        try:
            return str(p.relative_to(self.project_dir))
        except ValueError:
            return str(p)

    def remember_video(
        self,
        video_path: Path,
        transcript_path: Path | None = None,
        transcript_text: str = "",
        visually_ingested: bool = False,
        transcript_fingerprint: str | None = None,
    ) -> None:
        resolved_video = video_path.resolve()
        resolved_transcript = transcript_path.resolve() if transcript_path is not None else None
        self.active_video_path = resolved_video
        self.active_video_key = self.video_memory_key(resolved_video)
        self.active_video_visually_ingested = visually_ingested
        if visually_ingested:
            self._visually_ingested_video_keys.add(self.active_video_key)
        self.active_transcript_path = resolved_transcript
        self.active_transcript_text = transcript_text
        self.active_transcript_fingerprint = (
            transcript_fingerprint
            if transcript_fingerprint is not None
            else self.file_fingerprint(resolved_transcript)
        )
        if resolved_transcript is not None or transcript_text:
            self.remember_transcript_for_video(
                resolved_video, resolved_transcript, transcript_text,
                transcript_fingerprint=self.active_transcript_fingerprint,
            )

    def remember_transcript_for_video(
        self,
        video_path: Path,
        transcript_path: Path | None = None,
        transcript_text: str = "",
        transcript_fingerprint: str | None = None,
    ) -> None:
        resolved_video = video_path.resolve()
        resolved_transcript = transcript_path.resolve() if transcript_path is not None else None
        if resolved_transcript is not None or transcript_text:
            fingerprint = (
                transcript_fingerprint
                if transcript_fingerprint is not None
                else self.file_fingerprint(resolved_transcript)
            )
            self._transcripts_by_video[self.video_memory_key(resolved_video)] = (
                resolved_transcript, transcript_text, fingerprint,
            )

    def transcript_for_video(self, video_path: Path) -> TranscriptMemory | None:
        return self._transcripts_by_video.get(self.video_memory_key(video_path))

    def has_visual_ingest_history(self) -> bool:
        return bool(self._visually_ingested_video_keys)

    def video_was_visually_ingested(self, video_path: Path) -> bool:
        return self.video_memory_key(video_path) in self._visually_ingested_video_keys

    def active_video_changed(self) -> bool:
        if self.active_video_path is None or self.active_video_key is None:
            return False
        return self.video_memory_key(self.active_video_path) != self.active_video_key

    def reset_active_state(self) -> None:
        """Drop the cross-call "active video / transcript" memory.

        The MCP server keeps one RunContext for the life of the process, and the
        harness reuses that same process across ``/clear`` and compaction. So
        ``video_watch_segment`` called with no ``video_path`` would otherwise
        silently reuse the video from a *previous* conversation. Call this when
        a session boundary is known (e.g. wired to the harness session id) so a
        cleared conversation can never leak into the next one."""
        self.active_video_path = None
        self.active_video_key = None
        self.active_video_visually_ingested = False
        self.active_transcript_path = None
        self.active_transcript_text = ""
        self.active_transcript_fingerprint = None
        # Per-video caches are keyed by content fingerprint, so they cannot
        # collide across distinct videos; keep them to avoid re-transcribing.

    def video_memory_key(self, video_path: Path) -> str:
        resolved = video_path.resolve()
        try:
            stat = resolved.stat()
        except OSError:
            return str(resolved)
        return f"{resolved}|{stat.st_size}|{stat.st_mtime_ns}"

    @staticmethod
    def file_fingerprint(path: Path | None) -> str | None:
        """path|size|mtime_ns 指纹; 文件不存在/不可 stat 返回 None。"""
        if path is None:
            return None
        resolved = Path(path).resolve()
        try:
            stat = resolved.stat()
        except OSError:
            return None
        return f"{resolved}|{stat.st_size}|{stat.st_mtime_ns}"


def reject_input_output_collision(input_path: Path | None, output_path: Path | None) -> None:
    """Raise ValueError if ``output_path`` would overwrite ``input_path``.

    The cut/trim/speed/snapshot tools all run ffmpeg with ``-y`` (overwrite
    without asking). A user (or agent) pointing output_path at the source file
    would silently destroy the source footage. Resolved-path comparison catches
    both the identical string and the ``./a.mp4`` vs ``out/../a.mp4`` cases."""
    if input_path is None or output_path is None:
        return
    try:
        if Path(input_path).resolve() == Path(output_path).resolve():
            raise ValueError(
                f"refusing to write output over the input file ({output_path}); "
                "choose a different output_path"
            )
    except OSError:
        # One of the paths doesn't exist yet (the normal case for output) —
        # resolve() on a missing path still normalizes lexically, so a real
        # collision is still caught; an OSError here means we can't tell, so
        # don't block the operation.
        return
