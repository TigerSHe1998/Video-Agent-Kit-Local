---
description: 执行一个视频剪辑任务。
skills: video-edit-agent
---

处理这个视频剪辑任务。任务说明就是用户在会话中的自然语言描述（触发本命令的消息及其上下文）。

- 默认素材位置：当前工作目录（如果任务说明指定了文件或文件夹，优先使用指定路径）

必须遵守：

1. 按 `video-edit-agent` skill 的 Material Discovery 规则解析素材路径：未指定路径时优先读当前工作目录的 `materials_manifest.json`，缺失或不完整再用 skill 里的 `find` 命令盘点；排除 `out/`、`.video_agent/` 等生成目录，识别视频/音频/图片，不要假设单一固定源文件。
2. 根据任务说明和素材清单选择主源素材与辅助素材。对每个会参与剪辑的视频/音频素材调用 `inspect_media`；对高光截取、混剪、预告片类任务，或需要场景边界/静音/黑场辅助规划的任务，紧接着调用 `analyze_media(input_path)` 获取候选片段提示。多素材时把清单和关键 metadata 汇总写入 `out/media.json`。
3. 对需要理解语音内容的视频/音频，紧接着调用 `speech_transcribe(input_path)` 生成对应 transcript（单主视频可用 `out/transcript.json`；多素材用 `out/transcripts/<素材名>.json`）。默认走本地 ASR（Moss Transcribe WebUI，带段级时间戳与说话人聚类；词级时间戳不受支持，相关下游功能自动降级）。静音音频返回的空转录是有效结果，不是失败。近静音输入若转出成段文本，会被自动标注 `suspected_hallucination`（数字静音可触发模型幻觉），据此决定是否采信。只有当本地 ASR 不可用（`transcribe` CLI 不在 PATH）时才允许继续无转录流程，并把该限制记入 `out/report.md`；不要伪造转录。
4. 必须对每个需要做剪辑判断的源视频调用 `video_ingest(video_path, transcript_path=...)` 看完整视频；它会按内嵌抽帧逻辑把视频变成带时间戳的 contact sheet，并附带匹配转录。你必须自己读取这些图片后再做剪辑判断。图片素材需要用文件读取/视觉检查，不能忽略。
5. 只有在需要确认剪切点、动作连续性、字幕遮挡、表情或画面细节时，才调用 `video_watch_segment` 局部高 fps 复看；如果已经知道多个疑点，用 `segments=[{"start":..., "end":...}, ...]` 一次批量复看。
6. 生成 timeline JSON 后先 `validate_timeline`，再 `render_preview`，最后 `qc_preview(video_path="out/preview.mp4", timeline_path="out/timeline.json")`。项目式 `tracks[]` timeline 会按 sequence 画布、轨道、`timeline_start`、字幕/文字、图片叠加和音频混音编译成 preview，并生成 `out/preview.render_report.json`、`out/preview.render_plan.json`、`out/preview.edit_decisions.json`（后两份是项目式渲染的产物；legacy 顶层 `clips[]` 的 debug 渲染只写 render report）。
7. 如需视觉自检 preview，用 `video_ingest(video_path="out/preview.mp4", output_json="out/preview_ingest.json", ...)`，不要覆盖源素材的观察包。active video / transcript 复用与覆盖保护语义见 `video-edit-agent` skill 与 AGENT_REFERENCE；速记：文件被覆盖后隐式复看会报错，显式传当前 `video_path` 即可直接复看。
8. 如 QC 或内容自检发现问题，用 `timeline_diff` 记录修复，再重新验证。

自动化造数据场景下不要等待用户澄清或确认。缺信息时基于任务说明和视频内容做保守选择，并把假设写入输出报告。
