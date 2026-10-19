from __future__ import annotations



TOOL_SCHEMAS: dict[str, dict] = {
    "inspect_media": {
        "description": "Inspect a video or audio file with ffprobe and return structured media metadata plus a concise summary and technical risk list. Duration prefers format duration, falls back to stream duration, and ignores NaN/Infinity values. The summary includes duration, dimensions, fps, codecs, audio layout, pixel/color metadata, and rotation when available; risks flag no-audio/no-video, odd dimensions, possible VFR, HDR transfer metadata, and large-source cost.",
        "inputSchema": {
            "type": "object",
            "required": ["input_path"],
            "properties": {
                "input_path": {"type": "string"},
                "output_json": {"type": "string"}
            },
            "additionalProperties": False
        }
    },
    "analyze_media": {
        "description": "Run lightweight deterministic planning analysis on a source video/audio file using FFmpeg/ffprobe. It detects scene-change timestamps, derives scene-boundary candidate segments, scans black ranges and silence ranges, and writes a JSON report. These are planning hints only; verify candidate segments with video_ingest/video_watch_segment before final timeline decisions.",
        "inputSchema": {
            "type": "object",
            "required": ["input_path"],
            "properties": {
                "input_path": {"type": "string"},
                "output_json": {"type": "string"},
                "scene_threshold": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "exclusiveMaximum": 1,
                    "default": 0.3
                }
            },
            "additionalProperties": False
        }
    },
    "video_ingest": {
        "description": "Sample the complete video into timestamped contact-sheet images and return them as MCP image content for the main model to inspect directly. Standard flow: run transcribe first and pass its output as transcript_path, so every contact sheet is paired with matching speech text; omit transcript arguments only when transcribe reported that the local ASR is unavailable (a transcript-less ingest while ASR is available gets a reminder in the result). Call this once before planning any video edit, and call it again if the video file at that path is overwritten or replaced. Explicit transcript_path must exist. In an MCP session, re-ingesting a video without transcript arguments reuses the transcript previously remembered for that same video fingerprint when available. Full-ingest session state is recorded only after the observation package is successfully produced; failed sampling does not count as full-video visual evidence. The tool is idempotent for the same video/prompt/transcript/settings, preserves every sampled visual observation without blank/near-duplicate folding, and always includes the matching transcript when provided. Default observation JSON is out/video_ingest.json (pass an explicit output_json for multi-source runs). If the result lists non-inlined image paths, read them before making visual decisions. This tool does not call any model API.",
        "inputSchema": {
            "type": "object",
            "required": ["video_path"],
            "properties": {
                "video_path": {"type": "string"},
                "transcript_path": {"type": "string"},
                "transcript_text": {"type": "string"},
                "prompt": {"type": "string"},
                "output_json": {"type": "string"},
                "save_frames_dir": {"type": "string"}
            },
            "additionalProperties": False
        }
    },
    "video_watch_segment": {
        "description": "Sample one or more local video segments at higher FPS and return timestamped contact-sheet images for the main model to inspect directly. Provide either start_time+end_time for one window, or segments=[{start,end}, ...] for a batch, plus fps (required); passing neither is a tool error, and when segments is provided it takes precedence over start_time/end_time. Pass video_path to watch any existing video directly; a prior full video_ingest is not required for explicit video_path calls. If video_path is omitted, the tool uses the current active video from the session; if that active file has been overwritten or replaced since it was last observed, implicit segment watch returns an error. Explicit transcript_path must exist. Passing a video_path without transcript arguments reuses the transcript previously remembered for that same video fingerprint when available instead of clearing it. Start/end values must be numeric. Windows that clearly exceed source duration return an error instead of being silently shortened. The tool tracks already-watched windows per (start, end, fps): only a same-times same-fps request is skipped with status=skipped_duplicate and no new sheets, so rewatching the same window at a different fps is a new observation; pass force=true to bypass the duplicate skip entirely. Overlapping windows produce warnings but are kept. Default observation JSONs land under out/watch/ (out/watch/<video>_watch.json plus per-window suffixes), not the out/ root. Each window is capped at 60 seconds and one call at 180 seconds total; split longer spans into multiple calls. Matching transcript is attached automatically. Active-video session state is updated only after new contact sheets are successfully produced; failed or skipped-duplicate watches do not count as new visual evidence, and partial failures are flagged with a leading [ERROR] summary. If the result lists non-inlined image paths, read them before making visual decisions. This tool does not call any model API.",
        "inputSchema": {
            "type": "object",
            "required": ["fps"],
            "properties": {
                "start_time": {"type": "number", "minimum": 0},
                "end_time": {"type": "number", "minimum": 0},
                "video_path": {"type": "string"},
                "transcript_path": {"type": "string"},
                "transcript_text": {"type": "string"},
                "segments": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "required": ["start", "end"],
                        "properties": {
                            "start": {"type": "number", "minimum": 0},
                            "end": {"type": "number", "minimum": 0}
                        },
                        "additionalProperties": False
                    }
                },
                "fps": {"type": "number", "minimum": 0.1},
                "force": {
                    "type": "boolean",
                    "default": False,
                    "description": "Bypass the watched-window duplicate skip and rewatch the requested windows."
                },
                "prompt": {"type": "string"},
                "output_json": {"type": "string"},
                "save_frames_dir": {"type": "string"}
            },
            "additionalProperties": False
        }
    },
    "video_basic_operation": {
        "description": "Run one deterministic FFmpeg-backed basic video operation. Supported operations are trim, splice, speed, crop, scale, rotate, flip, and freeze_frame. Use this for source preparation or one-off media transforms; use render_preview for timeline-based preview rendering.",
        "inputSchema": {
            "type": "object",
            "required": ["operation"],
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["trim", "splice", "speed", "crop", "scale", "rotate", "flip", "freeze_frame"]
                },
                "input_path": {"type": "string"},
                "output_path": {"type": "string"},
                "work_dir": {"type": "string"},
                "start_time": {"type": "number", "minimum": 0},
                "end_time": {"type": "number", "minimum": 0},
                "duration": {"type": "number", "exclusiveMinimum": 0},
                "speed": {"type": "number", "minimum": 0.1, "maximum": 8.0},
                "reverse": {"type": "boolean", "default": False},
                "x": {"type": "number", "minimum": 0},
                "y": {"type": "number", "minimum": 0},
                "width": {"type": "integer", "minimum": 2},
                "height": {"type": "integer", "minimum": 2},
                "output_width": {"type": "integer", "minimum": 2},
                "output_height": {"type": "integer", "minimum": 2},
                "mode": {"type": "string", "enum": ["fit", "fill", "stretch"], "default": "fit"},
                "degrees": {"type": "number"},
                "direction": {"type": "string", "enum": ["horizontal", "vertical", "both"], "default": "horizontal"},
                "at_time": {"type": "number", "minimum": 0},
                "clips": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source": {"type": "string"},
                            "input_path": {"type": "string"},
                            "src": {"type": "string"},
                            "start": {"type": "number", "minimum": 0},
                            "end": {"type": "number", "minimum": 0},
                            "duration": {"type": "number", "exclusiveMinimum": 0},
                            "reason": {"type": "string"}
                        },
                        "additionalProperties": False
                    }
                }
            },
            "additionalProperties": False
        }
    },
    "subtitle_scout": {
        "description": "Cheap subtitle-placement scout for a local video. Samples representative frames, returns an overview strip plus a native-width caption-band strip, measures band luminance, reports shot cuts, and suggests concrete style overrides for contrast/font-size before building subtitles. This tool does not call any model API.",
        "inputSchema": {
            "type": "object",
            "required": ["video_path"],
            "properties": {
                "video_path": {"type": "string"},
                "preset": {
                    "type": "string",
                    "enum": ["shortform_zh", "broadcast", "broadcast_en"],
                    "default": "shortform_zh"
                },
                "style": {"type": "object"},
                "max_frames": {"type": "integer", "minimum": 2},
                "scene_threshold": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1},
                "frames_dir": {"type": "string"},
                "output_json": {"type": "string"}
            },
            "additionalProperties": False
        }
    },
    "subtitle_build": {
        "description": "Build a subtitle package from timed transcript segments and a source video. Produces subtitles.json plus ASS/SRT sidecars, using punctuation-aware and word-boundary-aware cue segmentation, font-aware width measurement, and silence-snapped cue boundaries. Read the returned cue table before rendering.",
        "inputSchema": {
            "type": "object",
            "required": ["transcript_path", "video_path"],
            "properties": {
                "transcript_path": {"type": "string"},
                "video_path": {"type": "string"},
                "preset": {
                    "type": "string",
                    "enum": ["shortform_zh", "broadcast", "broadcast_en"],
                    "default": "shortform_zh"
                },
                "style": {"type": "object"},
                "speaker_names": {"type": "object"},
                "language": {"type": "string"},
                "silence_db": {"type": ["number", "string"], "description": "Silence threshold in dB, or 'auto'."},
                "min_silence_seconds": {"type": "number", "exclusiveMinimum": 0},
                "snap_window_seconds": {"type": "number", "exclusiveMinimum": 0},
                "output_json": {"type": "string"},
                "output_ass": {"type": "string"},
                "output_srt": {"type": "string"}
            },
            "additionalProperties": False
        }
    },
    "subtitle_render": {
        "description": "Render subtitles from subtitles.json onto a local video. Burn-in mode re-emits ASS from cues[] every time so hand edits to subtitles.json take effect; soft mode muxes a mov_text subtitle track.",
        "inputSchema": {
            "type": "object",
            "required": ["video_path", "subtitles_path"],
            "properties": {
                "video_path": {"type": "string"},
                "subtitles_path": {"type": "string"},
                "output_path": {"type": "string"},
                "mode": {"type": "string", "enum": ["burn", "soft", "both"], "default": "burn"},
                "ass_path": {"type": "string"},
                "srt_path": {"type": "string"},
                "crf": {"type": "integer", "minimum": 0, "maximum": 51}
            },
            "additionalProperties": False
        }
    },
    "subtitle_qc": {
        "description": "Validate a subtitle package and optionally sample evidence frames from the rendered video. Checks cue timing, visibility, overlap, line overflow using real font metrics, reading-speed warnings, and transcript coverage. The report carries issues[] (severity-tagged) plus explicit errors[]/warnings[] arrays and counts. Evidence frames must be inspected by the agent.",
        "inputSchema": {
            "type": "object",
            "required": ["subtitles_path"],
            "properties": {
                "subtitles_path": {"type": "string"},
                "video_path": {"type": "string"},
                "output_json": {"type": "string"},
                "frames_dir": {"type": "string"},
                "max_evidence_frames": {"type": "integer", "minimum": 1}
            },
            "additionalProperties": False
        }
    },
    "condense_index": {
        "description": "Index a long spoken video for condensing. Splits timed transcript into sentence-like units using punctuation and detected pauses, finds disfluency/repetition/topic hints, computes a lossless compression floor, runs a coarse visual survey, and writes speech_index.json plus a full markdown unit table.",
        "inputSchema": {
            "type": "object",
            "required": ["video_path"],
            "properties": {
                "video_path": {"type": "string"},
                "transcript_path": {"type": "string", "default": "out/transcript.json"},
                "language": {"type": "string"},
                "unit_pause_seconds": {"type": "number", "exclusiveMinimum": 0},
                "max_unit_seconds": {"type": "number", "exclusiveMinimum": 0},
                "min_silence_seconds": {"type": "number", "exclusiveMinimum": 0},
                "silence_db": {"type": ["number", "string"], "description": "Silence threshold in dB, or 'auto'."},
                "max_gap": {"type": "number", "minimum": 0},
                "visual_survey": {"type": "boolean", "default": True},
                "survey_frames": {"type": "integer", "minimum": 1},
                "scene_threshold": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1},
                "frames_dir": {"type": "string"},
                "max_table_rows": {"type": "integer", "minimum": 1},
                "output_json": {"type": "string"},
                "output_table": {"type": "string"}
            },
            "additionalProperties": False
        }
    },
    "condense_plan": {
        "description": "Turn a speech index keep/drop list into cut clips and joins. Snaps clip edges to detected pauses, tightens long pauses inside kept runs, flags continuity and word-boundary risks, and writes condense_plan.json, condense_timeline.json, condense_script.md, condensed_transcript.json, and condense_redline.json.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "index_path": {"type": "string", "default": "out/speech_index.json"},
                "video_path": {"type": "string"},
                "keep": {
                    "type": "array",
                    "items": {"type": ["string", "object"]},
                    "description": "Unit ids/ranges such as 'u012-u031', or explicit {start,end} ranges."
                },
                "drop": {"type": "array", "items": {"type": "string"}},
                "tighten_pauses": {"type": "boolean", "default": True},
                "drop_fillers": {"type": ["boolean", "string"], "description": "false/off, 'hard', or 'aggressive'."},
                "max_gap": {"type": "number", "minimum": 0},
                "lead_in": {"type": "number", "minimum": 0},
                "lead_out": {"type": "number", "minimum": 0},
                "min_clip": {"type": "number", "exclusiveMinimum": 0},
                "snap_window": {"type": "number", "exclusiveMinimum": 0},
                "min_silence_seconds": {"type": "number", "exclusiveMinimum": 0},
                "silence_db": {"type": ["number", "string"], "description": "Silence threshold in dB, or 'auto'."},
                "target_duration": {"type": "number", "exclusiveMinimum": 0},
                "target_ratio": {"type": "number", "exclusiveMinimum": 0},
                "output_json": {"type": "string"},
                "timeline_json": {"type": "string"},
                "script_md": {"type": "string"},
                "output_transcript": {"type": "string"},
                "redline_json": {"type": "string"}
            },
            "additionalProperties": False
        }
    },
    "condense_render": {
        "description": "Render a condensed spoken video from condense_plan.json. Cuts each planned clip through FFmpeg, normalizes all segments to one canvas/fps/pixel format, joins with hard cuts or short dissolves, and writes a render report with output join times.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan_path": {"type": "string", "default": "out/condense_plan.json"},
                "video_path": {"type": "string"},
                "output_path": {"type": "string"},
                "work_dir": {"type": "string"},
                "join": {"type": "string", "enum": ["hard", "dissolve"], "default": "hard"},
                "dissolve_seconds": {"type": "number", "exclusiveMinimum": 0},
                "crf": {"type": "integer", "minimum": 0, "maximum": 51},
                "output_width": {"type": "integer", "minimum": 2},
                "output_height": {"type": "integer", "minimum": 2}
            },
            "additionalProperties": False
        }
    },
    "condense_qc": {
        "description": "Quality-check a condensed video against its condense plan. Verifies duration/hash binding/audio presence, measures join audio energy and visual jump severity, returns join frame/waveform evidence sheets, and writes condense_qc_report.json.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan_path": {"type": "string", "default": "out/condense_plan.json"},
                "video_path": {"type": "string", "default": "out/condensed.mp4"},
                "render_report_path": {"type": "string"},
                "frames_dir": {"type": "string"},
                "max_evidence_joins": {"type": "integer", "minimum": 1},
                "output_json": {"type": "string"}
            },
            "additionalProperties": False
        }
    },
    "diarize_audit": {
        "description": "Audit speaker labels in an existing transcript without changing text. Returns speaker share, roster sample windows, name cues from transcript text, suspect turn windows, and a soft verdict to guide visual speaker-attribution repair.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "transcript_path": {"type": "string", "default": "out/transcript.json"},
                "window_pad": {"type": "number", "minimum": 0},
                "window_gap": {"type": "number", "minimum": 0},
                "max_windows": {"type": "integer", "minimum": 1},
                "output_json": {"type": "string"}
            },
            "additionalProperties": False
        }
    },
    "diarize_relabel": {
        "description": "Apply corrected time-range to speaker assignments to a transcript, rewriting only speaker labels on segments/words and splitting segments at word-level speaker boundaries when safe. It never edits transcript text.",
        "inputSchema": {
            "type": "object",
            "required": ["assignments"],
            "properties": {
                "transcript_path": {"type": "string", "default": "out/transcript.json"},
                "assignments": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["start", "end", "speaker"],
                        "properties": {
                            "start": {"type": "number", "minimum": 0},
                            "end": {"type": "number", "minimum": 0},
                            "speaker": {"type": "string"}
                        },
                        "additionalProperties": False
                    }
                },
                "snap_seconds": {"type": "number", "minimum": 0},
                "output_path": {"type": "string"}
            },
            "additionalProperties": False
        }
    },
    "video_read_frames": {
        "description": "Read a few original-resolution frames, optionally cropped/upscaled, for inspecting small on-screen text such as name plates, lower-thirds, UI labels, or slide text. Use video_watch_segment for motion; use this when the task is reading detail.",
        "inputSchema": {
            "type": "object",
            "required": ["video_path"],
            "properties": {
                "video_path": {"type": "string"},
                "timestamps": {"type": "array", "items": {"type": "number", "minimum": 0}},
                "start_time": {"type": "number", "minimum": 0},
                "end_time": {"type": "number", "minimum": 0},
                "count": {"type": "integer", "minimum": 1},
                "region": {
                    "type": ["string", "object"],
                    "description": "Preset name such as full/name_plate/lower_third/bottom_left/bottom_right/top_left/top_right/center, or {left,top,right,bottom} fractions."
                },
                "upscale": {"type": "number", "minimum": 1, "maximum": 4},
                "max_width": {"type": "integer", "minimum": 64},
                "max_frames": {"type": "integer", "minimum": 1, "maximum": 48},
                "output_dir": {"type": "string"}
            },
            "additionalProperties": False
        }
    },
    "validate_timeline": {
        "description": "Validate EDL/timeline project JSON before rendering. Requires ffprobe for source-duration bounds; source duration is read from format duration with stream duration fallback and cached per source file. Pipeline timelines must be JSON objects with non-empty project, assets[], sequence{} or output_canvas{}, tracks[], and at least one video/main track; legacy top-level clips[] timelines validate in legacy mode — structural and clip-level checks still fail on real defects, the project contract is downgraded to a warning, and render_preview can consume them through the legacy sequential path, but pipeline outputs must still be project-style. It validates project/sequence/output_canvas/assets/markers/transitions metadata when provided (transition entries accept type/kind/name); tracks and clips must be objects. Checks required clip source/start/end-or-duration/reason fields, rejects boolean/NaN/Infinity time values, checks end-duration consistency, source existence, source-duration bounds, optional timeline placement/effects/transition metadata, and writes a report with the current timeline hash. In tracks[] timelines, clips on non-video tracks (subtitle/overlay/callout plans) may provide non-empty text instead of a media source; video/main track clips always require a source.",
        "inputSchema": {
            "type": "object",
            "required": ["timeline_path"],
            "properties": {
                "timeline_path": {"type": "string"},
                "output_json": {"type": "string"}
            },
            "additionalProperties": False
        }
    },
    "render_preview": {
        "description": "Render a preview video from timeline JSON using FFmpeg/ffprobe. Project-style tracks[] timelines are rendered as a basic editing project: video/main clips are placed on a black sequence canvas by timeline_start with boundary padding, original video audio plus audio/music/voiceover tracks are internally pre-baked into full-length positioned wav beds before mixing, subtitle/text tracks are composited with drawtext, and image/overlay tracks are composited with overlay enable windows. The renderer writes preview.render_report.json, preview.render_plan.json, and preview.edit_decisions.json for traceability. It supports simple fade transition_in/transition_out, clip speed, volume, opacity, output_canvas/sequence canvas and fps. Pipeline timelines must satisfy the project contract; top-level clips[] are legacy/debug compatibility only. Unsupported unknown track types are recorded in the render plan rather than silently rendered.",
        "inputSchema": {
            "type": "object",
            "required": ["timeline_path"],
            "properties": {
                "timeline_path": {"type": "string"},
                "output_path": {"type": "string"},
                "work_dir": {"type": "string"},
                "output_width": {"type": "integer", "minimum": 2},
                "output_height": {"type": "integer", "minimum": 2}
            },
            "additionalProperties": False
        }
    },
    "qc_preview": {
        "description": "Run deterministic QC on a preview/final video: ffprobe metadata, black frame scan, short edit-boundary black-frame scan (short blacks are attributed to video-track cut points within ±0.08 s), silence scan, freeze-frame scan, volume peak/mean scan, audio stream coverage, timeline/render hash binding, and expected duration/resolution/fps checks against the timeline. Media probe failures and scan failures are blocking; black/silence/freeze/clipping/very-low-volume findings become warning issues unless they indicate a scan failure; audio ending before the video is an error. The silence/volume scans only run when an audio stream exists; a video without any audio stream is reported as a warning. Pass timeline_path so the QC report can prove which timeline produced the preview and compare output media against timeline sequence expectations.",
        "inputSchema": {
            "type": "object",
            "required": ["video_path"],
            "properties": {
                "video_path": {"type": "string"},
                "timeline_path": {"type": "string"},
                "output_json": {"type": "string"}
            },
            "additionalProperties": False
        }
    },
    "timeline_diff": {
        "description": "Create an auditable timeline diff from repair instructions and optionally apply it to a timeline. patch must be a JSON object when provided, and apply must be a boolean. Supported patch operations: set_timeline_fields (project metadata except clips/tracks), set_track_fields, add_assets, update_assets, remove_asset_indices, add_tracks, insert_tracks, replace_tracks, remove_track_indices, update_clips (shallow field merge; null deletes a field), replace_clips, remove_clip_indices, move_clips, insert_clips, and add_clips. replace/update/remove/move indices refer to the pre-patch flat clip order; asset and track indices refer to their own arrays. Structural track operations (add/insert/replace/remove tracks) cannot be mixed with clip operations in the same patch; split them into separate timeline_diff calls. replace/update are applied first, then remove in reverse order, then at most one move_clips entry, then insert/add new clips. move_clips cannot be combined with remove_clip_indices, cannot contain multiple moves in one patch, and cannot address clips inserted by the same patch. Duplicate remove/replace/update indices, remove-and-replace/update conflicts, out-of-range indices, non-object clips/fields, an empty apply patch, or mixing structural track operations with clip operations are validation errors. When apply=true, patch operations and the patched timeline are validated first; invalid patches write the diff report but do not modify the original timeline. In tracks[] timelines, add_clips prefers the first video/main track, while insert/move may specify track_index.",
        "inputSchema": {
            "type": "object",
            "required": ["timeline_path", "instructions"],
            "properties": {
                "timeline_path": {"type": "string"},
                "instructions": {"type": "string"},
                "patch": {"type": "object"},
                "apply": {"type": "boolean", "default": False},
                "output_json": {"type": "string"}
            },
            "additionalProperties": False
        }
    }
}


EXTRA_TOOL_SCHEMAS: dict[str, dict] = {'detect_shots': {'description': 'Detect shot boundaries across the whole video with PySceneDetect ContentDetector and '
                                 'cache them as a shots.json timeline (segment_version cover-all-v2). Shots cover the '
                                 'full video contiguously with no gaps: sub-min_len shots are merged into the previous '
                                 'shot (or accumulated into the first real shot when they open the video), so adjacent '
                                 'shots always satisfy end == next start. Output JSON fields '
                                 '(video/duration/video_signature/segment_version/threshold/min_len/frame_skip/fps/n_shots/shots[{shot_id,start,end,dur}]) '
                                 'are the shared contract consumed by downstream shot-driven editing decisions. '
                                 'If output_json already exists and its video '
                                 'signature (path|size|mtime) plus all detection parameters match, the cache is reused '
                                 'and the result reports reused=true; pass force=true to redetect. frame_skip>0 speeds '
                                 'up detection by skipping frames at the cost of cut precision. This tool does not '
                                 'call any model API; requires ffprobe and the scenedetect package.',
                  'inputSchema': {'type': 'object',
                                  'required': ['input_path'],
                                  'properties': {'input_path': {'type': 'string'},
                                                 'output_json': {'type': 'string', 'default': 'out/shots.json'},
                                                 'threshold': {'type': 'number',
                                                               'exclusiveMinimum': 0,
                                                               'default': 27.0,
                                                               'description': 'ContentDetector cut sensitivity; lower '
                                                                              'detects more cuts.'},
                                                 'min_len': {'type': 'number',
                                                             'minimum': 0,
                                                             'default': 0.8,
                                                             'description': 'Shots shorter than this are merged into '
                                                                            'neighbors instead of dropped, keeping '
                                                                            'coverage gap-free.'},
                                                 'frame_skip': {'type': 'integer', 'minimum': 0, 'default': 0},
                                                 'force': {'type': 'boolean',
                                                           'default': False,
                                                           'description': 'Ignore an existing matching cache and '
                                                                          'redetect.'}},
                                  'additionalProperties': False}}}


TOOL_SCHEMAS.update(EXTRA_TOOL_SCHEMAS)


SPEECH_TRANSCRIBE_SCHEMA = {
    "description": (
        "Create a timestamped transcript for a video/audio file through the local transcribe CLI "
        "(Moss Transcribe WebUI backend). The CLI attaches to a running WebUI service on "
        "VE_TRANSCRIBE_HOST:VE_TRANSCRIBE_PORT (default 127.0.0.1:8390) or starts one automatically. "
        "Timestamps are segment-level with speaker diarization from the local model (pass "
        "diarize=false to skip diarization: no speaker labels are emitted, even though the engine "
        "reports a single cluster id); word-level timing "
        "is NOT provided by the Moss models (words stay empty; downstream falls back to segment "
        "timing). The model detects "
        "language itself (the language argument is recorded only). Long media is auto-split "
        "server-side (default 30 minutes, VE_TRANSCRIBE_AUTOSPLIT; 0 disables) with timestamps "
        "corrected to the full-media timeline; when split, speaker labels are per-split clusters "
        "(p{part}_{speaker}) — the same person may carry different labels across parts. Transient "
        "service failures retry by default; input/usage errors do not. Silent audio returns a valid "
        "empty transcript flagged silent_audio=true; a near-silent input that still comes back with "
        "text is flagged suspected_hallucination=true with the measured input loudness (the model "
        "can hallucinate fluent text on digital silence — verify against the audio). The tool's "
        "data summary carries "
        "word_level_supported (false with the Moss models) so word_count=0 reads as expected, "
        "never as a transcription failure."
    ),
    "inputSchema": {
        "type": "object",
        "required": ["input_path"],
        "properties": {
            "input_path": {"type": "string"},
            "output_json": {"type": "string"},
            "provider": {
                "type": "string",
                "enum": ["auto", "local_asr", "local"],
                "default": "auto",
                "description": "ASR provider. Use auto; the local backend is the only implementation.",
            },
            "language": {
                "type": "string",
                "default": "",
                "description": "Recorded in the transcript payload only; the local model detects language itself.",
            },
            "diarize": {
                "type": "boolean",
                "default": True,
                "description": "Enable speaker diarization (local model clustering). False emits no speaker labels at all (the engine still reports a single cluster id, which would be misleading).",
            },
            "autosplit_min": {
                "type": "integer",
                "minimum": 0,
                "maximum": 180,
                "description": "Auto-split long audio into N-minute chunks (0 = off, default 30 / VE_TRANSCRIBE_AUTOSPLIT).",
            },
            "backend": {
                "type": "string",
                "description": "Inference device name for the local service (see `transcribe --list-backend`; default VE_TRANSCRIBE_BACKEND).",
            },
            "timeout_seconds": {"type": "number", "exclusiveMinimum": 0},
            "retries": {
                "type": "integer",
                "minimum": 0,
                "maximum": 10,
                "description": "Extra retry attempts for transient service failures.",
            },
            "retry_count": {"type": "integer", "minimum": 0, "maximum": 10},
            "retry_backoff_seconds": {
                "type": "number",
                "minimum": 0,
                "description": "Initial exponential backoff before ASR retries (default 10).",
            },
            "work_dir": {"type": "string"},
        },
        "additionalProperties": False,
    },
}



TOOL_SCHEMAS["speech_transcribe"] = SPEECH_TRANSCRIBE_SCHEMA
TOOL_SCHEMAS["transcribe"] = {
    **SPEECH_TRANSCRIBE_SCHEMA,
    "description": "Compatibility alias for speech_transcribe. Prefer speech_transcribe for new integrations.",
}
