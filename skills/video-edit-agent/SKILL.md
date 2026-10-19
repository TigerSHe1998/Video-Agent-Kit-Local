---
name: video-edit-agent
description: 视频剪辑总控入口：素材发现、任务分类、剪辑全流程闭环。
when_to_use: >
  Default entrypoint for ANY video edit request (剪视频/剪辑/剪出/加字幕/压缩这个视频): discovers materials,
  classifies the task (multi-material assembly -> video-edit-assembly; single-source speech condensing or
  talking-head subtitles -> video-speech-workflows), then runs the common workflow: inspect_media,
  speech_transcribe (local ASR), video_ingest full-video frame inspection, timeline.json, validate_timeline,
  render_preview, qc_preview, timeline_diff repair loop, out/report.md. Enforces the file contract and
  completion bar. Use for highlight extraction, reframing/crop, cleanup, general timeline edits.
---

# Video Edit Agent

You are the global controller for video editing tasks. Match the user's editing request, choose the right workflow, keep decisions traceable, and produce a concrete rendered preview/final video.

## Routing

Start here for every video-edit task. Classify the task before detailed planning.

Use `video-edit-assembly` when the user provides multiple materials and asks to combine, assemble, reorder, select, remove duplicates, make a montage, make a social-media cut, create a highlights reel from a batch, or build a coherent story from a source pack. Invoke the Skill tool with:

```json
{"skill": "video-edit-assembly", "args": "<user task, material assumptions, hard constraints>"}
```

Multi-material assembly has priority over the single-source routes below. If a batch/pack edit also needs captions, first produce the assembled/cut output under the assembly/common workflow, then subtitle the rendered output so cue times match the final video.

Two-phase runs (edit first, then subtitle the rendered output): by then `out/media.json` may already describe the *assembled deliverable's* sources rather than the raw material. That is fine — `inspect_media` also auto-splits per-source probes into `out/media/<source>.json` when the default path already holds a different input, so per-source metadata stays recoverable. Do not delete the earlier file to "fix" the contract; cross-reference it in `out/report.md` instead.

For single-source voiced-video tasks, route to `video-speech-workflows` when its modes are a direct match. Invoke it with the same Skill tool shape and include the mode in args:

| Mode | Use when |
|---|---|
| `speech-condense` | One long unscripted recording (podcast, interview, panel, lecture, livestream, verbose talking-head clip) must be cut shorter while preserving continuity, removing filler/repetition/dead air, or hitting a target length. |
| `talking-head-subtitles` | A talking-head, monologue, or voice-over video needs accurate burned-in captions, better subtitle timing/segmentation, speaker-labelled captions, karaoke highlighting, bilingual lines, or subtitle legibility/placement repair. |
| `video-pipeline` | A single voiced video needs an end-to-end stage plan that may combine condensing or editing with captions, speaker differentiation, karaoke, translation, or terminology notes, or the prompt is underspecified ("process this video", "add suitable subtitles") and requires selecting compatible stages. |

Examples:

```json
{"skill": "video-speech-workflows", "args": "mode=speech-condense; <user task, source path, target length/ratio if any, hard constraints>"}
{"skill": "video-speech-workflows", "args": "mode=talking-head-subtitles; <user task, source path, caption style/language requirements if any>"}
{"skill": "video-speech-workflows", "args": "mode=video-pipeline; <user task, source path, requested stages/features, hard constraints>"}
```

If the task does not match a specialized route, follow the common workflow in this skill. Highlight extraction, reframing/crop, cleanup, and general timeline edits still use the common workflow unless a more specific skill exists. Record assumptions in `out/report.md`.

Do not wait for strategy confirmation during automated data runs. If the task is underspecified, make conservative assumptions and record them in `out/report.md`.

## Common Hard Rules

1. **Discover materials before editing.** If the user names files or folders, inspect those paths first. Otherwise treat the current working directory as the user's material folder. Prefer `./materials_manifest.json` when present, then scan with `find` if the manifest is absent or incomplete. Exclude generated/runtime directories such as `out`, `.video_agent`, `.claude`, `.git`, and hidden config files. Do not assume `/workspace/input/video.mp4` or a single source file.
2. **Inspect every relevant source.** Identify videos, audio files, images, and other assets. Inspect every video/audio file that may participate in the edit before timeline decisions. Visually inspect image assets when relevant.
3. **Do not plan cuts from transcript alone.** Visual editing decisions require visual evidence from `video_ingest` or `video_watch_segment`.
4. **Protect speech.** When a source has audio and speech may matter, run `speech_transcribe(input_path)` before visual inspection and pass the transcript to `video_ingest`. Silent-audio empty transcripts are valid. If the local ASR is unavailable, continue only with explicit limitations in `out/report.md`; never invent transcript content.
5. **Read returned images yourself.** Video tools do not call any model API; they return timestamped contact sheets and matching transcript text to this conversation.
6. **Attach matching transcript to visual inspection.** Full-video inspection gets the full transcript when available; local rewatch gets transcript text for that time range when available.
7. **Use local rewatch only for uncertainty.** Use `video_watch_segment` for cut points, continuity, expressions, occlusion, small motion, subtitle/callout placement, or other local details. Batch multiple known windows with `segments=[...]`.
8. **Validate before render; QC after render.** A timeline is not accepted until `validate_timeline`, `render_preview`, and `qc_preview` have run.
9. **Use canonical contract paths.** Do not substitute aliases such as `out/qc.json` for `out/preview_qc_report.json`, or `out/preview_media.json` for source `out/media.json`.
10. **Repairs must be auditable.** Use `timeline_diff` for timeline changes after QC or self-review failures, then rerun validation, render, and QC.

## File Contract

All contract artifacts live under `out/` relative to the project directory. Conversation text does not count as completion state.

| File | What | When |
|---|---|---|
| `out/media.json` | Material inventory plus structured metadata for source video/audio files from `inspect_media`. | Before transcript or visual inspection. |
| `out/transcript.json` or `out/transcripts/*.json` | ASR transcript for spoken source media. Use per-source files for multi-source tasks and summarize them in `out/media.json`. | Before full-video visual inspection when ASR is available and speech may matter. |
| `out/media_analysis.json` or `out/media_analysis/*.json` | Optional deterministic source-analysis hints: scene boundaries, candidate segments, black ranges, and silence ranges. | Before timeline planning when useful. |
| `out/video_ingest.json` or `out/ingest/*.json` | Full-video contact-sheet observation packages with transcript attached. Use `out/video_ingest.json` for one primary source; use distinct source packages such as `out/ingest/<source>.json` for multi-source tasks. | Before timeline planning. |
| `out/timeline.json` | Project timeline: project metadata, assets, sequence/canvas, tracks, clips, ranges, reasons, and planned subtitles/overlays/effects. | Before rendering. |
| `out/timeline_validation.json` | Current timeline validation report with the timeline hash. | Before rendering and after every timeline change. |
| `out/preview.mp4` | Rendered preview from the current timeline. | Before QC/self-review. |
| `out/preview.render_report.json` | Render binding report with timeline hash and output hash. | Produced by `render_preview`. |
| `out/preview.render_plan.json` | Deterministic render plan derived from the timeline. | Produced by `render_preview` for project-style `tracks[]` renders (legacy `clips[]` renders write only the render report). |
| `out/preview.edit_decisions.json` | Structured edit-decision sidecar. | Produced by `render_preview` for project-style `tracks[]` renders. |
| `out/preview_qc_report.json` | Deterministic technical QC report. | After preview render. |
| `out/report.md` | Final summary: task, assumptions, material decisions, output, QC status, unresolved risks. | Last step. |

When calling tools, pass `output_json` explicitly whenever needed to preserve canonical paths. For multiple sources, inspect each source into `out/media/<source>.json`, transcribe spoken sources into `out/transcripts/<source>.json`, ingest videos into `out/ingest/<source>.json`, and summarize source metadata and selection decisions in `out/media.json`.

`out/video_ingest.json`, `out/ingest/*.json`, and `video_watch_segment` JSON files are tool observation packages. Do not overwrite them with subjective summaries. Put candidate clips, risks, review notes, and final conclusions in `out/report.md` or separate review artifacts.

## Common Workflow

Run these phases unless a specialized skill gives a stricter workflow:

`discover materials -> classify task -> inspect source media -> transcribe speech where needed -> video_ingest selected source videos -> read all returned sheets/images -> local rewatch only where needed -> write project timeline -> validate_timeline -> render_preview -> qc_preview -> self-review -> timeline_diff repair loop -> report`

### Material Discovery

If the user names a file or folder, resolve it relative to the current working directory unless it is absolute. If the user does not specify a source path, assume all user-provided materials are in the current working directory. First read `./materials_manifest.json` when it exists; if it is missing, stale, or incomplete, scan:

```bash
find . -maxdepth 2 -type f \
  -not -path './out/*' \
  -not -path './.video_agent/*' \
  -not -path './.claude/*' \
  -not -path './.git/*' \
  -not -name '.*' | sort
```

Classify assets as video, audio, image, or other. Source paths must come from the material inventory.

### Timeline Requirements

Write `out/timeline.json` as an editing project file. Pipeline runs require a JSON object with non-empty `project`, `assets[]`, `tracks[]`, and `sequence` or `output_canvas`. Include at least one typed `video` or `main` track. Top-level `clips[]` exists only for legacy/debug compatibility and should not be used for automated runs.

Each renderable video/main clip must include:

```json
{
  "source": "source.mp4",
  "start": 12.4,
  "end": 24.8,
  "timeline_start": 0.0,
  "reason": "The clip introduces the conflict and has clean visual continuity."
}
```

The reason must cite editorial purpose, not just restate timestamps. All times must be finite numbers, not booleans, `NaN`, or `Infinity`. If both `end` and `duration` are present, they must agree. Non-video tracks may use non-empty `text` instead of `source`.

The preview renderer consumes basic project timelines: video/main tracks by `timeline_start`, source clip audio and audio/music/voiceover tracks as full-length positioned audio beds, subtitle/text tracks with drawtext, and image/overlay tracks by enable windows. Advanced NLE features such as arbitrary keyframes, masks, nested sequences, complex transition curves, and full grading are project metadata unless explicitly supported by the renderer.

### Render, QC, And Repair

Run:

1. `validate_timeline(timeline_path="out/timeline.json", output_json="out/timeline_validation.json")`
2. `render_preview(timeline_path="out/timeline.json", output_path="out/preview.mp4")`
3. `qc_preview(video_path="out/preview.mp4", timeline_path="out/timeline.json", output_json="out/preview_qc_report.json")`

QC checks file/playability, timeline/render binding, expected duration/resolution/fps, black frames including short edit-boundary black frames, silence, freeze frames, audio stream coverage, and audio volume. Treat errors as blockers. Treat warnings as review items; either fix them or explain why they are intended/acceptable in `out/report.md`.

For visual self-review of `out/preview.mp4`, call `video_ingest` with a separate `output_json` such as `out/preview_ingest.json`; do not overwrite source observation packages. If repairs are needed, use `timeline_diff`. Structural track operations and clip operations must be split into separate `timeline_diff` calls.

## Tool Policy

- `inspect_media`: required for every source video/audio file that may participate.
- `analyze_media`: optional deterministic planning aid; it does not replace visual inspection.
- `transcribe`: required when speech may matter or the user asks not to cut speech; silent results are valid.
- `video_ingest`: required full-video visual inspection for source videos used in edit decisions.
- `video_watch_segment`: targeted high-FPS rewatch; returns images for this main agent to inspect.
- `video_basic_operation`: optional deterministic source-prep tool. Do not treat it as a replacement for timeline validation and preview rendering.
- `subtitle_scout`, `subtitle_build`, `subtitle_render`, `subtitle_qc`: use through `video-speech-workflows` mode `talking-head-subtitles` for straight captioning, or after a rendered edit when the final deliverable also needs subtitles.
- `condense_index`, `condense_plan`, `condense_render`, `condense_qc`: use through `video-speech-workflows` mode `speech-condense` for single-source speech tightening; do not substitute these for multi-material assembly timelines.
- `diarize_audit`, `diarize_relabel`, `video_read_frames`: speaker-attribution support tools for `video-speech-workflows` when speaker identity matters.
- `detect_shots`: deterministic shot-boundary timeline for edit planning; cache-aware.
- `validate_timeline`, `render_preview`, `qc_preview`: required before accepting output from the common timeline workflow.
- `timeline_diff`: required for repair-loop auditability.

## Completion Bar

For tasks routed to `video-edit-assembly` or `video-speech-workflows`, use the selected specialized skill's additional or replacement completion bar. The common completion bar below applies when this skill runs the generic timeline workflow directly.

The task is complete only when:

- `out/media.json` exists;
- source `video_ingest` observations exist (`out/video_ingest.json` for one primary source, or source-specific `out/ingest/*.json` / `out/video_ingest_<source>.json` for multi-source tasks);
- `out/timeline.json` exists and validates;
- `out/timeline_validation.json` exists, passes, and covers the current `out/timeline.json`;
- `out/preview.mp4` exists and is the rendered artifact that QC covers;
- `out/preview.render_report.json`, `out/preview.render_plan.json`, and `out/preview.edit_decisions.json` exist and cover the current timeline/preview (the plan and decisions sidecars are project-style `tracks[]` render outputs; legacy `clips[]` debug renders write only the render report);
- `out/preview_qc_report.json` exists, passes, covers the current `out/preview.mp4`, and records the current `out/timeline.json` hash;
- hard QC errors are resolved or explicitly blocked;
- `out/report.md` exists, is non-empty, and records assumptions, material decisions, outputs, QC status, and remaining risks.
