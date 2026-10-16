---
name: sys-setup
description: 新机器体检系统级依赖：ffmpeg 编码器/滤镜、中文字体、转录 CLI。
when_to_use: >
  Use BEFORE the first edit run on a new machine, or whenever a tool fails in an environment-shaped way:
  `ffmpeg not found`, `Unknown encoder 'libx264'`, `No such filter: 'subtitles'`, `no font file found for family`,
  字幕烧成豆腐块/方块, speech_transcribe 报转录 CLI 未找到, transcribe not found. Also use when asked to
  检查环境 / 装依赖 / 配国内源 / setup / doctor. System-level only: ffmpeg/ffprobe with libx264, aac,
  libmp3lame and libass/libfreetype filters, a CJK font whose cmap truly covers Chinese, the local transcribe
  CLI (Moss Transcribe WebUI) on PATH, free disk space. Python deps are NOT checked here (owned by the
  plugin venv bootstrap; a ModuleNotFoundError fix is mcp/bootstrap.py --ensure, never a global pip install).
  Windows-only plugin; install guidance uses China mirrors by default.
---

# System Setup (sys-setup)

This skill covers the **system-level** dependencies only. Python packages are
owned by the plugin's venv bootstrap (`mcp/bootstrap.py`): the `.venv` is
created inside the plugin directory on the first MCP/CLI call, refreshed when
`requirements.txt` changes, and deleted with the plugin on uninstall. Never
pip-install anything to fix plugin problems — if a python package is missing,
the fix is `python3 "<plugin_root>/mcp/bootstrap.py" --ensure` (or simply
calling any tool once).

## What must be present, and what dies without it

| Requirement | Dead without it |
|---|---|
| `ffmpeg` + `ffprobe` on PATH, **with libx264 / aac / libmp3lame** | ingestion and probing (Phase 1 can't start); missing encoder = the render dies at the last step, after everything upstream was paid for |
| that ffmpeg **built with libass + libfreetype** (`subtitles`/`ass`/`drawtext` filters) | subtitle burn-in and title cards |
| a **CJK font whose cmap really covers Chinese** | Chinese subtitles/titles. This one fails *silently* — see the tofu warning below |
| the local `transcribe` CLI (Moss Transcribe WebUI) on PATH | `speech_transcribe`; without it there is no transcript, so every speech-driven decision is guesswork — see below |
| ≥ 5 GB free on the project drive | preview/final mp4 + frame sheets live in the project dir; running out mid-render is the failure mode |

### Speech runs locally through the transcribe CLI

Transcription goes through the local **Moss Transcribe WebUI** deployment's
`transcribe` CLI. The CLI attaches to a running service (default
`127.0.0.1:8390`) or starts one automatically and stops it after the job.
There is no key, no cloud, nothing to configure beyond the CLI being reachable:

- **The only requirement is that `transcribe` resolves on PATH** (or
  `VE_TRANSCRIBE_CMD` points at the executable). The doctor probes exactly
  that; the row is soft (`只降级不致命`).
- The service lifecycle (start/model load) is the CLI's job, not the
  environment's — do not try to pre-start or health-check the WebUI service
  during setup. A first transcription may take a few minutes while the model
  loads; that is normal, not a failure.
- `VE_TRANSCRIBE_HOST` / `VE_TRANSCRIBE_PORT` / `VE_TRANSCRIBE_AUTOSPLIT` /
  `VE_TRANSCRIBE_BACKEND` are optional tuning knobs; see `.env.example`.

## Mirrors — use them by default, not as a fallback

The official CDN routes from mainland China are routinely slow enough to time
out. A timeout there doesn't look like a network problem — it looks like "the install ran for
ten minutes and then failed", which gets misdiagnosed as a broken package every
time. **So a mirror is the first choice, not the recovery step.** The doctor
already puts the mirror into the command it prints (and uses); when you install
by hand, use these:

| What | First choice | Alternative |
|---|---|---|
| ffmpeg | `winget install --id Gyan.FFmpeg -e` (static build from gyan.dev; no mirror concept — be patient or proxy) | `conda install -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge ffmpeg` — Tsinghua mirror, no system package manager touched |
| CJK font | Nothing to install — Windows ships 微软雅黑 (msyh.ttc) | Download NotoSansSC from `github.com/notofonts/noto-cjk` into any directory and point `VE_FONT_DIRS` at it |
| local transcribe CLI | Not a download — it ships with the user's Moss Transcribe WebUI deployment; the fix is PATH or `VE_TRANSCRIBE_CMD` | — |

Notes that matter in practice:

- **Do not blindly `pip install` to fix an import error from a plugin tool.**
  Plugin code runs inside the plugin `.venv`; a package missing there means
  the venv is stale or broken — run `bootstrap.py --ensure`, don't touch any
  other environment.
- **Never uninstall or downgrade system packages automatically.** The system
  ffmpeg, someone's patched toolchain — print the fix command, let the user
  decide.

## Workflow

1. Run the doctor script with an absolute path (Bash timeout ≥ 120s):

   ```bash
   python3 <plugin_root>/skills/sys-setup/scripts/env_doctor.py          # probe + what's needed
   ```

   The plugin root is recorded in `.video_agent/env_report.json`
   (`plugin_root` field) after a doctor run. Always call the script by its
   absolute path — the plugin's installed location differs per machine.

2. Read its output **in order**: each line says what's missing, which
   capability dies without it, and an example install command. `[~]`/`[未探测]` lines are skipped checks — fix their
   dependency (`needs=`) first and re-run; don't install blind.
3. Install what's missing **from the table above**, preferring mirrors.
   The doctor prints example commands; the user runs them (or asks you to,
   for user-level installs only).
4. Re-run the doctor to confirm; the report lands in
   `.video_agent/env_report.json`.

## Rules

- **A font that resolves is not a font that works.** The doctor reads the
  font's cmap — if it says the resolved font has no Chinese coverage, treat it
  as missing even though a path was printed (a tofu-box render still exits 0).
- **"装了" ≠ "能用"**: a stripped/static ffmpeg build can be on PATH yet lack
  libass — the doctor probes encoders/filters, not just presence. Only
  treat a check as passing when the doctor says so.
- **System-level installs are the user's call.** The doctor never installs
  ffmpeg or fonts by itself; don't run package managers on the user's behalf
  beyond what they've explicitly approved.
- **Missing python modules are NOT an env_doctor finding.** The venv bootstrap
  owns them. If a tool fails with `ModuleNotFoundError`, run
  `python3 <plugin_root>/mcp/bootstrap.py --ensure` and retry.

## Fonts

Font lookup is centralised in `mcp/ve_tools/fonts.py`, searched in this order:

1. `VE_FONT_DIRS` (os.pathsep-separated) — explicit override, always wins
2. Windows system font dirs — `%SystemRoot%\Fonts` and the per-user
   `%LOCALAPPDATA%\Microsoft\Windows\Fonts`

**Windows normally needs no font install at all** — 微软雅黑 is found in step
2, and libass resolves it through DirectWrite. When a user has fonts somewhere
unusual, `VE_FONT_DIRS` is the answer — not a code change, and not copying
files into the system font directory.

## Anti-patterns

- Proceeding into a subtitle burn-in with ffmpeg missing libass, letting it
  surface as a mysterious tool error twenty rounds later.
- **Asking the user for an ASR key.** Transcription is local — there is no key
  to give; the only fix is the `transcribe` CLI being on PATH. Chasing a key
  wastes the user's turn and fixes nothing.
- Blaming the plugin for `speech_transcribe` timeouts before checking the local
  service — the first job on a fresh service pays the model-load cost (minutes),
  which is normal, not a hang.
- Treating a `[未探测]` line as a missing dependency, or a `[-]` (n/a) line as a defect.
- pip-installing into any global/system interpreter to "fix" the plugin — the
  plugin runs from its own `.venv`, so the global install fixes nothing.
