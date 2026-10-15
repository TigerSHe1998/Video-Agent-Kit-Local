---
name: env-setup
description: Checking and provisioning the machine's environment for the video-agent-kit-local plugin — probing for ffmpeg/ffprobe that actually carry the encoders and filters we render with (libx264/aac/libmp3lame, libass, libfreetype), python packages (mcp, cv2, numpy, PIL, jieba, fontTools, scenedetect) in the interpreter Claude Code really uses, a CJK font whose cmap truly covers Chinese, and the local transcribe CLI (Moss Transcribe WebUI) being resolvable — then installing what is missing (China mirrors by default) or telling the user exactly what to install. Works on macOS, Windows and Linux. Use BEFORE the first edit run on a new machine, or whenever a tool fails in an environment-shaped way — ModuleNotFoundError, `ffmpeg not found`, `Unknown encoder 'libx264'`, `No such filter: 'subtitles'`, `no font file found for family`, 字幕烧成豆腐块/方块, speech_transcribe 超时或报服务错误. Also use when asked to 检查环境 / 装依赖 / 配国内源 / setup / doctor.
---

# Env Setup

Claude Code is a generic harness — it ships **no** ffmpeg, no CJK font, no python
media stack. Everything this kit needs is per-machine, and provisioning it is
this skill's job. A run started on a half-provisioned machine does not fail
cleanly: it burns the whole observe→timeline phase and then dies at
`render_preview`, or worse, *succeeds* while burning subtitles as tofu boxes
(□□□) because `fc-match` handed us a font with no Chinese glyphs and ffmpeg
exited 0.

**One entry point** — everything else here is judgement about its output:

```bash
python3 <plugin_root>/skills/env-setup/scripts/env_doctor.py          # probe + what's needed
python3 <plugin_root>/skills/env-setup/scripts/env_doctor.py --fix    # + install the auto-safe items
```

(Windows: `python` instead of `python3` if that's what's on PATH.) Plugin root is
announced at session start and written to `.video_agent/plugin_root`. Always call
it by absolute path — cwd is the project directory, not the skill directory. A
full probe is a few seconds; give Bash ≥ 120s anyway, because a blocked network
spends its timeouts before answering.

## What must be present, and what dies without it

The script decides *state* and prints the exact requirement + an example command
for **the current OS**. This table is only for judging *impact*:

| Requirement | Dead without it |
|---|---|
| Python ≥ 3.10, and `mcp` (1.x), `opencv-python-headless`, `numpy`, `pillow`, `jieba`, `fonttools`, `scenedetect` in **that same interpreter** | `mcp` → every tool (server won't start); cv2/numpy/PIL → `video_ingest`, contact sheets, `qc_preview` scans, i.e. all visual evidence; jieba → `subtitle_build` line breaking; fontTools → the pre-render tofu check; scenedetect → `detect_shots` shot boundaries |
| `ffmpeg` + `ffprobe` on PATH, **with libx264 / aac / libmp3lame** | ingestion and probing (Phase 1 can't start); missing encoder = the render dies at the last step, after everything upstream was paid for |
| that ffmpeg **built with libass + libfreetype** (`subtitles`/`ass`/`drawtext` filters) | subtitle burn-in and title cards |
| a **CJK font whose cmap really covers Chinese** | Chinese subtitles/titles. This one fails *silently* — see the tofu warning above |
| the local `transcribe` CLI (Moss Transcribe WebUI) on PATH | `speech_transcribe`; without it there is no transcript, so every speech-driven decision is guesswork — see below |

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

Version floors are the only hard numbers; how the tool gets installed is the
user's choice — package manager, installer, conda, whatever the machine already
uses.

## Mirrors — use them by default, not as a fallback

Most users of this plugin are in mainland China, where the official PyPI CDN and
GitHub-hosted Homebrew bottles are routinely slow enough to time out. A timeout
there doesn't look like a network problem — it looks like "the install ran for
ten minutes and then failed", which gets misdiagnosed as a broken package every
time. **So a mirror is the first choice, not the recovery step.** The doctor
already puts the mirror into the command it prints (and uses); when you install
by hand, use these:

| What | First choice | Alternative |
|---|---|---|
| pip | 清华 `https://pypi.tuna.tsinghua.edu.cn/simple` — `pip install -i https://pypi.tuna.tsinghua.edu.cn/simple <pkg>` | 阿里云 `https://mirrors.aliyun.com/pypi/simple/` |
| ffmpeg | **macOS**: `export HOMEBREW_BOTTLE_DOMAIN=https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles` then `brew install ffmpeg`. **Any platform / no root**: `conda install -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge ffmpeg` — cleanest path, no system package manager touched. | apt sources → Tsinghua/USTC (system-wide change); Windows `winget`/gyan.dev has no mirror concept (use conda) |
| CJK font | Linux `apt-get install fonts-noto-cjk` (apt mirror helps). mac/Windows already ship 苹方/微软雅黑 — install nothing. | Download NotoSansSC from `github.com/notofonts/noto-cjk` into any directory and point `VE_FONT_DIRS` at it (works without root) |
| local transcribe CLI | Not a download — it ships with the user's Moss Transcribe WebUI deployment; the fix is PATH or `VE_TRANSCRIBE_CMD` | — |

Notes that matter in practice:

- Making it permanent is the user's call, not yours by default:
  `pip config set global.index-url <url>` writes their **global** config and
  affects every other project on the machine. The doctor prints it; only run it
  if the user agrees — a one-off `-i <url>` needs no such permission.
- **Mirrors lag.** If a package version genuinely isn't on the mirror, the fix is
  to drop `-i` for that one install and go to the official source — not to
  conclude the environment is broken.
- Outside mainland China these mirrors are pointless (and slower).
  `VE_PIP_INDEX=<url>` overrides the pip index the doctor uses; set it to the
  official URL to opt out entirely.
- A mirror fixes slowness, **a proxy fixes blockage** — different problems. Try
  the mirror first; only if the mirror itself is unreachable is it a
  proxy/firewall question.

## Workflow

`doctor → read the requirements → --fix the auto items → tell the user what's left → re-run doctor → report`

1. **Run the doctor first, always.** Never install from this file's tables or
   from memory — on a provisioned machine the correct action is *nothing*, and a
   speculative install can downgrade a working package (or, with opencv, break
   the one that worked).
2. **`--fix` covers only the user-space, idempotent subset**: pip installs into
   the running interpreter. It deliberately does **not** install ffmpeg, fonts,
   or uninstall anything — those are system-level or destructive, the right
   method differs per machine (and per permission level), and guessing wrong is
   worse than reporting. For those, **give the user the requirement and the
   suggested command, and let them run it.**
3. **Fix in the printed order.** An item whose prerequisite failed prints
   `[未探测]` instead of a guessed verdict — don't run a fix for an unprobed
   item; fix its prerequisite and re-run.
4. **`·` means "not applicable on this OS", not "broken"**, and
   `只降级不致命` means exactly that: the two speech-channel rows and disk headroom
   don't block a task. Everything else is a hard gap.
5. **After a PATH-changing install, the new tool is invisible to the current
   shell** (most visibly on Windows). If the doctor still can't see ffmpeg the
   user just installed, that's the reason — have them restart the terminal (or
   Claude Code) rather than reinstalling.
6. **Re-run the doctor at the end** — not the fix commands' exit codes.
   `pip install` can succeed while the import still fails (a wheel mismatched to
   the platform). The run writes `.video_agent/env_report.json` as the receipt,
   so **don't re-probe every turn**; re-probe after installing something or
   after a tool fails.
7. **Report in capability terms, then stop if something's dead.** "ffmpeg has no
   libass → subtitle burn-in unavailable; the recap can't ship. Install a full
   build and re-run `/env-check`." Do not enter a phase whose tools are dead and
   improvise around it.

## Rules

- **Install only what the doctor flagged.** Extra packages are how a working
  environment drifts into an unreproducible one.
- **Never substitute for a missing dependency.** No cv2 does not mean "extract
  frames with ffmpeg and eyeball the filenames"; no transcript does not mean
  "infer the dialogue from the visuals"; no CJK font does not mean "render the
  subtitles in English". A dead capability is a report line, not a workaround.
- **`scenedetect` must be installed with `--no-deps`.** It declares
  `opencv-python` (the GUI build), which overwrites the same `cv2` package
  directory as `opencv-python-headless` and leaves both half-broken. The doctor
  prints the two-step command; don't "simplify" it.
- **Never auto-uninstall opencv.** If GUI+headless are both installed the doctor
  reports it and prints the fix, because the GUI build may belong to another
  project on that machine. That's the user's call.
- **A font that resolves is not a font that works.** `fc-match` never fails: ask
  it for an uninstalled Chinese family and it returns DejaVu Sans, which has no
  CJK glyphs, and libass then renders blanks while ffmpeg exits 0. The doctor
  reads the cmap for exactly this reason — if it says the resolved font has no
  Chinese coverage, treat it as missing even though a path was printed.
- **Adding a dependency to this plugin means adding a `Check` to
  `env_doctor.py` in the same change** (and to `requirements.txt`). The
  doctor is the single source of truth for dependency diagnostics — a
  dependency that isn't a `Check` is one nobody hears about until it crashes.

## Fonts: the one platform difference worth knowing

Font lookup is centralised in `mcp/ve_tools/fonts.py`, searched in this order:

1. `VE_FONT_DIRS` (os.pathsep-separated) — explicit override, always wins
2. the Linux dirs the kit has always used (`/root/.fonts`, `/usr/share/fonts`)
3. the platform's own font dirs — `/System/Library/Fonts`, `~/Library/Fonts`
   (macOS), `%SystemRoot%\Fonts`, `%LOCALAPPDATA%\Microsoft\Windows\Fonts`
   (Windows)
4. `fc-list :lang=zh` when fontconfig exists, cmap-verified

So **macOS and Windows normally need no font install at all** (苹方 / 微软雅黑
are found in step 3, and libass resolves them through CoreText/DirectWrite).
Linux images without `fonts-noto-cjk` are the case that actually needs work.
When a user has fonts somewhere unusual, `VE_FONT_DIRS` is the answer — not a
code change, and not `sudo cp` into `/usr/share/fonts`.

## Anti-patterns

- Prescribing one platform's install method (`apt-get …`) to a user on another OS
  — the doctor already picked the right example for their machine; if they'd
  rather use their own method, that's fine, the requirement is what matters.
- Reading this skill's tables and installing from them without running the doctor.
- Treating a `[未探测]` line as a missing dependency, or a `·` (n/a) line as a defect.
- Auto-installing system-level software (ffmpeg, fonts) or uninstalling packages
  on the user's machine without being asked.
- Retrying a stalled `pip install` unchanged on a machine that needs a mirror
  (or a proxy) — the second attempt fails the same way, ten minutes later.
- Declaring the environment fixed off exit codes without a re-probe.
- Re-running the full doctor every turn "to be safe" — `.video_agent/env_report.json`
  holds the last verdict.
- Proceeding into a render or a recap with ffmpeg missing libass, letting it
  surface as a mysterious tool error twenty rounds later.
- **Asking the user for an ASR key.** Transcription is local — there is no key
  to give; the only fix is the `transcribe` CLI being on PATH. Chasing a key
  wastes the user's turn and fixes nothing.
- Blaming the plugin for `speech_transcribe` timeouts before checking the local
  service — the first job on a fresh service pays the model-load cost (minutes),
  which is normal, not a hang.
