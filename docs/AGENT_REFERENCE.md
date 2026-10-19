# video-agent-kit-local

`video-agent-kit-local` 是自动化视频剪辑工具包（本地化版本）。它把视频剪辑需要的特殊能力封装成 MCP 工具和 CLI：媒体探查、本地语音转录（Moss Transcribe WebUI）、完整视频抽帧观察、局部高 fps 复看、基础视频操作、timeline 校验、预览渲染、QC 和可审计修复。语音转录在本机执行，不需要任何云端凭据。

## 设计边界

- 不假设主模型可以直接接收视频文件；视频会先按固定规则抽帧并拼成带时间戳的 contact sheet，再通过 MCP 图片结果返回给宿主主 Agent。
- 不依赖任何插件目录之外的脚本：抽帧索引逻辑完整内置在 `mcp/ve_tools/video_observe.py`，本工具包不会构造 `image_url` 请求，也不会在工具内部调用任何外部推理服务。
- 当前只处理用户提供的视频/音频素材，不处理素材库检索，也不做图像/视频生成。
- 自动化造数据场景下不等待用户澄清或确认。

## MCP 工具

- `inspect_media`：ffprobe 媒体探查；总体时长优先读 format duration，缺失时回退到 stream duration；`NaN` / `Infinity` 不会被当成有效时长。
- `speech_transcribe`：本地语音转录工具，通过 Moss Transcribe WebUI 的 `transcribe` CLI 执行（CLI 自动对接运行中的服务，无服务时自动拉起）。本地模型提供段级时间戳和说话人分离（词级时间戳是 Moss 模型不支持的，`words` 恒为空，下游按既有降级路径处理）；模型自行判定语言（`language` 入参仅记录）。`diarize=false` 时不产出任何说话人标签——引擎在 diarize off 下仍会返回单一 speaker_id，插件会把它剥掉并落 `diarization_disabled: true`，避免"关了聚类却带恒定标签"误导下游。长音频默认按 30 分钟服务端自动分段（`VE_TRANSCRIBE_AUTOSPLIT`，0 关闭），时间戳修正为全片绝对时间；分段时说话人标签带 `p{段}_` 前缀，跨段同一人物可能有不同标签。服务瞬时错误默认额外重试 1 次；用法/输入错误不重试。毫秒时间戳会归一成秒；静音音频返回带 `silent_audio` 标注的有效空转录；**近静音输入却转出非空文本时会被标注 `suspected_hallucination: true` 并附实测响度**（数字静音会触发上游模型幻觉，交叉验证不删内容、只留痕，判断权在下游）。`transcribe` 保留为兼容 alias。
- `video_ingest`：完整视频抽帧并返回 contact sheet，必须先跑；宿主主 Agent 自己看图并形成理解。显式传入的 `transcript_path` 必须存在；不传 transcript 才表示允许无转录观察；在同一个 MCP 会话里，如果重新观察此前已绑定过 transcript 的同一视频指纹，会复用该视频对应的 transcript。同一路径的视频文件如果被覆盖或替换，必须重新调用 `video_ingest`。只有成功产出 observation package 后，工具才会把该视频记为已 full-ingest；抽帧失败不会推进会话状态。
- `video_watch_segment`：局部高 fps 复看；单窗口用 `start_time/end_time/fps`，多窗口用 `segments=[{start,end}, ...]` + `fps`，返回片段 contact sheet 和匹配转录。显式传 `video_path` 可直接复看任意存在的视频，不要求先跑完整 `video_ingest`。不带 `video_path` 时默认使用会话里的 active video；如果 active 文件被覆盖或替换，隐式复看会报错并要求显式传当前 `video_path` 或重新 `video_ingest`。显式传入的 `transcript_path` 必须存在；在同一个 MCP 会话里，如果显式传入此前已绑定过 transcript 的同一视频指纹且未传 transcript 参数，会复用该视频对应的 transcript。非数字时间参数会返回结构化错误；明显超过视频时长的窗口会报错，不会静默截断成较短观察。窗口台账按 `(start, end, fps)` 判重：只有时间和 fps 都相同的窗口才会被跳过，换一个 fps 重看同一窗口是新的观察；确有需要时可传 `force=true` 跳过判重。只有成功产出新 contact sheet 后，局部复看才会更新 active-video 会话状态；抽帧失败或 `skipped_duplicate` 不算新的视觉证据，多窗口部分失败时整体结果会带 `[ERROR]` 前缀汇总。默认观察 JSON 落在 `out/watch/` 子目录（`out/watch/<video>_watch.json` 及逐窗口后缀文件），不再散在 `out/` 根目录。
- `video_basic_operation`：确定性基础操作封装，支持 `trim`、`splice`、`speed`、`crop`、`scale`、`rotate`、`flip`、`freeze_frame`。它适合源素材预处理或单步变换；timeline 成片仍应走 `validate_timeline` / `render_preview`。
- `validate_timeline`：校验 timeline 项目 JSON；依赖 ffprobe 检查源素材时长边界，时长优先读 format duration，缺失时回退到 stream duration，且同一源文件的时长探测会缓存。除了旧版线性 `clips[]`，也支持更接近剪辑软件项目文件的 `project` / `sequence` / `output_canvas` / `assets` / `tracks` / `markers` / `transitions` / clip effects 元数据校验。项目契约（非空 `project`/`assets[]`/`tracks[]` + `sequence`/`output_canvas` + 视频轨）只对项目式 `tracks[]` timeline 是硬门槛；legacy 顶层 `clips[]` 走 **legacy 模式**——结构与 clip 级校验照常报错，契约缺失降级为一条 warning，`render_preview` 的线性分支可以消费它（validation 报告带 `mode: "project" | "legacy"`）。`tracks[]` timeline 中非视频轨的 clip（字幕 / overlay / callout 计划）可以用非空 `text` 代替媒体 `source`；视频 / main 轨 clip 和顶层 `clips[]` 仍必须有 `source`。
- `render_preview`：用 FFmpeg / ffprobe 渲染预览；render report 会记录 timeline hash 和 output hash。顶层 `clips[]` 仍按 legacy/debug 线性拼接渲染；项目式 `tracks[]` 会按 sequence 画布和 `timeline_start` 编译成基础多轨预览：主视频铺到画布并在片尾补少量末帧降低边界闪帧，源视频音频与 `audio/music/voiceover` 轨先烘焙成全片长度、已按时间线定位的 wav bed，再非归一化混音，`subtitle/text` 轨用 `drawtext` 烧录，`overlay/image` 轨按启用窗口叠加。父轨道没有 `type/name/track_type` 时不会被默认当作主视频轨；未知 track 类型会记录到 render plan 的 unsupported tracks 中，而不是声称完整渲染。
- `qc_preview`：硬性质量检查，包括容器、黑帧、短编辑边界黑帧（专项扫描把边界黑帧归因到视频轨切点 ±0.08s 内）、静音、冻结帧、音量、音频覆盖时长和空文件；媒体探测失败或显式传入的 `timeline_path` 不存在时直接报错，黑帧 / 静音 / 冻结 / 音量扫描失败会作为 QC error 写入报告。静音和音量扫描只在存在音轨时运行；完全没有音轨的成片会记一条 warning（整片无声），不会静默通过。
- `timeline_diff`：记录或应用 timeline 修复；`patch` 必须是 JSON object，`apply` 必须是 boolean。支持 `set_timeline_fields`、`set_track_fields`、`add_assets`、`update_assets`、`remove_asset_indices`、`add_tracks`、`insert_tracks`、`replace_tracks`、`remove_track_indices`、`update_clips`、`replace_clips`、`remove_clip_indices`、`move_clips`、`insert_clips` 和 `add_clips`。`update_clips` 是浅层字段合并，字段值为 `null` 表示删除该字段；`set_timeline_fields` 可改项目元数据但不能直接改 `clips/tracks`。`replace/update/remove/move` 索引按补丁前扁平 clip 顺序解释，asset/track 操作用各自数组内的索引解释；结构性 track 操作（add/insert/replace/remove tracks）不能和 clip 操作混在同一个 patch，必须拆成两次 `timeline_diff`。`move_clips` 每次 patch 最多一条，不能与删除操作混用，也不能指向同一个 patch 新插入的 clip。工具会先校验 patch 操作本身和 patch 后的 timeline。`apply=true` 遇到越界 index、非对象 clip/fields、空 patch、多条 move、结构性 track 操作和 clip 操作混用等非法操作时只写 diff 报告，不修改原 timeline。

默认文件契约为 `out/media.json`、`out/transcript.json`、源素材 `video_ingest` 观察包、`out/timeline.json`、`out/timeline_validation.json`、`out/preview.mp4`、`out/preview_qc_report.json` 和 `out/report.md`。其中 `out/transcript.json` 是本地 ASR 可用时的预期产物；如果本地 ASR 不可用，应在 `out/report.md` 记录限制并继续完成其余闭环。单素材任务可使用 `out/video_ingest.json`；多素材任务可以显式传 `output_json` 写到 `out/ingest/*.json` 或 `out/video_ingest_<source>.json`，并在 `out/media.json` 汇总。 本节与 `skills/video-edit-agent/SKILL.md` 的 File Contract 表是同一份契约的两种呈现——增删或改名产物时两处必须同步。

`out/media.json` 保留给源素材探测；当它已记录另一个输入时，不带 `output_json` 的 `inspect_media` 会自动改落 `out/media/<素材名>.json`，显式 `output_json` 永远尊重调用方。`qc_preview` 内部做媒体探测时会写 `out/preview_media.json` 这类 sidecar，不覆盖源素材 metadata。`validate_timeline` 和 `qc_preview` 会在报告中写入文件 hash；收尾前必须核对这些 hash 以拒绝已经过期的 validation / QC report（由 `video-edit-agent` skill 的完成门负责，不再依赖 hook）。

`schemas/video_observation.schema.json` 对应工具真实落盘的 observation package，即 `video_ingest` / `video_watch_segment` 生成的帧清单、sheet 清单、媒体元数据、抽帧参数和匹配转录。模型读图后的主观摘要、候选片段和风险判断应写入 `out/report.md` 或后续独立的 review 文件，不写回这个工具产物 schema。

除上文详列的 9 个核心工具外，插件还提供 13 个确定性工具（另有 `speech_transcribe` 的 `transcribe` 兼容别名，注册表合计 23 项），主要服务 `video-speech-workflows`：`analyze_media`（场景/黑场/静音分析；三次全片解码，长素材按需调用）、`subtitle_scout` / `subtitle_build` / `subtitle_render` / `subtitle_qc`（字幕四件套）、`condense_index` / `condense_plan` / `condense_render` / `condense_qc`（口播压缩四件套）、`diarize_audit` / `diarize_relabel`（说话人归属修复）、`video_read_frames`（原分辨率读帧）、`detect_shots`（镜头边界检测，缓存感知）。各自契约见对应 skill 文档。

## 插件使用

把本目录作为插件使用时，`.mcp.json` 会启动 `mcp/video_edit_server.py`（清单 `command` 为 `python3`，要求宿主 PATH 上有 3.10+ 的 Python）。注意：**安装后首次调用 MCP 工具时**会自动构建插件内 `.venv`（下载依赖约 1~2 分钟，取决于网速），宿主对该次调用的等待可能明显长于平时；之后就是秒级热启动。命令入口在：

```text
commands/video-edit.md
commands/env-check.md
skills/sys-setup/SKILL.md
skills/video-edit-agent/SKILL.md
skills/video-edit-assembly/SKILL.md
skills/video-speech-workflows/SKILL.md
```

`video-edit-agent` 是总控入口，负责素材发现、任务分类、通用文件契约、timeline
验证、渲染、QC 和修复闭环；`video-edit-assembly` 是多素材组装成片子流程，
用于多段视频/音频/图片素材的分组、选择、去重、排序和成片结构规划；
`video-speech-workflows` 处理单源语音压缩和字幕；`sys-setup` 只管系统级环境体检
（ffmpeg/字体/转录 CLI，仅 Windows），不碰剪辑任务；Python 依赖由 venv 自举自动管理。

### Python 依赖（自动，无需手工安装）

Python 依赖由 `mcp/bootstrap.py` 自动管理：**首次调用任何 MCP 工具或 CLI 时**，自动在插件目录内创建 `.venv` 并安装全部依赖（默认清华镜像，`VE_PIP_INDEX` 可换）；`requirements.txt` 变化时下次调用自动增量重装。`.venv` 随插件的安装/更新/卸载走——更新后首次调用会重建（预期行为），卸载时随插件目录一起删除，不碰全局 Python 环境。想预先构建（避免首次调用的等待）：

```bash
python3 "<插件根>/mcp/bootstrap.py" --ensure
```

### 系统级环境（ffmpeg / 字体 / 转录 CLI）

系统级依赖用体检脚本排查（仅 Windows）：

```bash
# 只体检：逐项报"要什么 / 缺了挂哪个能力 / 本平台怎么装"
python3 "<插件根>/skills/sys-setup/scripts/env_doctor.py"
# 体检 + 自动修可自动修的项（pip 依赖统一走 bootstrap 装进插件 .venv）
```

会话里也可以直接用 `/env-check`（对应 `sys-setup` skill）。体检结论落在
`.video_agent/env_report.json`，不必每轮重跑。

它查的不止"包在不在"：ffmpeg 有没有带 `libx264` / `aac` / `libmp3lame` 与
`libass`（缺了字幕烧录必挂，但只在渲染最后一步才炸）、本机中文字体的 cmap 是不是
真覆盖中文（解析出的字体若无中文覆盖，烧出来是豆腐块而 ffmpeg 退出码 0）、
以及本地转录 CLI（`transcribe`）在不在 PATH 上。Python 依赖不在体检范围（由 venv 自举管理）；其中字体覆盖度探测会在插件 `.venv` 解释器内执行（fontTools 在 venv 里）。

字体不在标准目录时不用改代码，把目录塞给 `VE_FONT_DIRS`（多个目录用
`os.pathsep` 分隔）即可；查找顺序见 `mcp/ve_tools/fonts.py`：`VE_FONT_DIRS` →
Windows 系统字体目录（自带微软雅黑，通常不需要装字体）。

MCP 工具调用的 `arguments` 顶层必须是 JSON object；如果入口传入数组、字符串等非对象参数，server 会直接返回结构化错误，不把异常泄漏成工具 traceback。

本插件不注册任何会话 hook，收尾与流程纪律全部由 skill 文档约束。环境检查走 `/env-check`（对应 `sys-setup` skill，管系统级依赖，装依赖一律官方源，不走第三方镜像）。剪辑任务的标准流程：先 `speech_transcribe` 产出 `out/transcript.json`，再带 `transcript_path` 调 `video_ingest`；收尾前按 `video-edit-agent` 的文件契约自查 `out/media.json`（或任一 `*media.json` sidecar）、`out/transcript.json`、源素材 `video_ingest` 观察包、`out/timeline.json`、`out/timeline_validation.json`、`out/preview.mp4`、`out/preview_qc_report.json` 和非空 `out/report.md` 是否闭环。单步任务（如只做 `inspect_media` 或 `speech_transcribe`）不触发契约。QC 报告必须由 `qc_preview(video_path="out/preview.mp4", timeline_path="out/timeline.json")` 生成，否则缺少 timeline hash 会被视为未闭环。

推荐工作流：

```text
video-edit-agent classify
  -> video-edit-assembly       # 多素材组装成片任务
  -> video-speech-workflows    # 单源语音压缩、说话人字幕、speech pipeline
  -> inspect_media
  -> transcribe
  -> video_ingest           # 一次性把完整视频变成可看的 contact sheet，附带完整转录
  -> video_watch_segment    # 只在需要时局部高 fps 复看；多个疑点应批量传 segments
  -> 生成 timeline.json
  -> validate_timeline
  -> render_preview
  -> qc_preview             # 传 video_path=out/preview.mp4 和 timeline_path=out/timeline.json
  -> 内容自检               # 如需看 preview，video_ingest 写到 out/preview_ingest.json，不覆盖源视频 out/video_ingest.json
  -> timeline_diff 修复 loop
```

注意：对 `out/preview.mp4` 调用 `video_ingest` 或显式 `video_watch_segment(video_path="out/preview.mp4", ...)` 后，后续不带 `video_path` 的 `video_watch_segment` 默认会复看 preview。若修复时还需要回看原始素材，应显式传原始 `video_path`；在同一个 MCP 会话里，工具会按视频指纹复用此前绑定过的源素材 transcript。如果某个 active video 路径上的文件内容被重新渲染、覆盖或替换，隐式局部复看会拒绝继续使用旧 active 状态；显式传当前 `video_path` 可直接复看当前文件，完整 `video_ingest` 只在需要全片 contact sheet 时再跑。CLI 每次调用都是新进程，不共享这份会话记忆，因此 CLI 直接复看时应显式传 `transcript_path` 或 `transcript_text`。

## 环境变量

所有配置统一走 `.env` 配置文件（KEY=VALUE），不需要在启动 shell 里手动 export。加载优先级：

1. 进程环境变量（最高，用于临时覆盖）；
2. 项目目录 `.env`（按项目覆盖）；
3. 插件根目录 `.env`（插件级默认值）。

完整可配置项参考 `.env.example`。

**语音转录完全本地化。** `speech_transcribe` 通过本机的 Moss Transcribe WebUI `transcribe` CLI 执行：CLI 自动对接运行中的服务（默认 `127.0.0.1:8390`），本机无服务时自动拉起并在任务结束后停止。不需要任何 key 或云端凭据；唯一的前置是 `transcribe` CLI 在 PATH 上（或显式指定路径）。

可选配置：

- `VE_TRANSCRIBE_CMD`：`transcribe` 可执行文件路径。默认从 PATH 解析（Windows 上是 `transcribe.bat`）。
- `VE_TRANSCRIBE_HOST` / `VE_TRANSCRIBE_PORT`：WebUI 服务地址，默认 `127.0.0.1:8390`。
- `VE_TRANSCRIBE_AUTOSPLIT`：长音频自动分段分钟数，默认 30，`0` 关闭（上限 180）。服务端精确切分并把时间戳修正为全片绝对时间；分段时说话人标签带 `p{段}_` 前缀。
- `VE_TRANSCRIBE_BACKEND`：推理设备名（`transcribe --list-backend` 查看），默认沿用运行中服务已加载的后端。

`speech_transcribe` 支持 `retries` / `retry_count` 表示服务瞬时错误的额外重试次数，默认 1，范围 0~10；`retry_backoff_seconds` 是指数退避初始秒数，默认 10 秒。只重试服务连接/中断等瞬时失败；用法/输入错误不会重试。

## CLI 使用

CLI 和 MCP 共用同一套实现：

```bash
python "<插件根>/cli/video_agent_tool.py" inspect_media --input_path input.mp4 --pretty
python "<插件根>/cli/video_agent_tool.py" transcribe --input_path input.mp4 --language zh --pretty
python "<插件根>/cli/video_agent_tool.py" video_ingest --args-file "<插件根>/examples/video_ingest_args.json" --pretty
python "<插件根>/cli/video_agent_tool.py" video_watch_segment --video_path input.mp4 --start_time 12 --end_time 16 --fps 6 --pretty
python "<插件根>/cli/video_agent_tool.py" video_basic_operation --operation trim --input_path input.mp4 --start_time 3 --end_time 8 --output_path out/trim.mp4 --pretty
python "<插件根>/cli/video_agent_tool.py" video_basic_operation --operation splice --args-json '{"clips":[{"source":"input.mp4","start":0,"end":3},{"source":"input.mp4","start":8,"end":12}],"output_path":"out/splice.mp4"}' --pretty
python "<插件根>/cli/video_agent_tool.py" validate_timeline --timeline_path out/timeline.json --pretty
python "<插件根>/cli/video_agent_tool.py" render_preview --timeline_path out/timeline.json --output_path out/preview.mp4 --pretty
# 可选：显式指定预览画布，适合横竖屏或不同分辨率素材混剪
python "<插件根>/cli/video_agent_tool.py" render_preview --timeline_path out/timeline.json --output_width 1080 --output_height 1920 --pretty
python "<插件根>/cli/video_agent_tool.py" qc_preview --video_path out/preview.mp4 --timeline_path out/timeline.json --pretty
```

CLI 的 `--args-json` / `--args-file` 必须是合法 JSON，且顶层必须是 JSON object；非法 JSON 或数组 / 字符串等非对象参数会在入口处直接报错。**Windows Git Bash 下 `--args-json` 的内嵌双引号会被 shell 剥掉**（常见报错 "must be valid JSON"；入口带了启发式修复，但不保证覆盖全部形态）——复杂参数请一律用 `--args-file`，它是跨 shell 可靠的形式。`--key value` 简写会自动解析 JSON object / array 字符串，所以可以直接传结构化 patch，例如 `--patch '{"add_clips":[...]}'`；复杂参数仍推荐使用 `--args-file`。

## 基础视频操作

`video_basic_operation` 是单步确定性基础操作工具，所有操作都通过 FFmpeg 落成可检查的视频文件，并写同名 `.basic_operation.json` 报告。当前支持：

- `trim`：`input_path` + `start_time` + `end_time` / `duration`，剪出一个时间范围。
- `splice`：`clips=[{"source","start","end"}]`，把多个片段归一到同一画布后拼接。
- `speed`：`speed=0.1..8.0`，可选 `reverse=true`；音频用 `atempo` 链同步变速。
- `crop` / `scale` / `rotate` / `flip`：基础构图和方向调整；`scale.mode` 支持 `fit`、`fill`、`stretch`。
- `freeze_frame`：`at_time` + `duration`，从某一帧生成静帧视频。

这个工具适合素材预处理、局部变换验证和构造中间素材。正式剪辑决策仍应写入 `out/timeline.json`，再走 `validate_timeline`、`render_preview`、`qc_preview`。

## 抽帧默认参数

这些是内置抽帧索引逻辑的默认值：

- `video_fps=2.0`
- `video_t_patch_size=2`
- `video_sampling_mode=prod`
- `max_video_frames=600`
- `video_frame_normalize_jpeg_quality=90`
- `video_label_mode=timestamp`
- `sheet_cols=4`
- `sheet_max_cells=24`
- `sheet_width=1568`
- `jpeg_quality=85`

输出形态是带时间戳的 contact sheet 图片。MCP 会内联前 16 张图片；如果更多，工具结果会列出剩余图片路径，宿主主 Agent 必须继续读取这些图片。sheet 上的时间戳始终按实际帧索引换算成源视频时间（能读到解码器 PTS 时优先用 PTS，兼容可变帧率素材）；局部复看也不改成片段内相对时间。`video_ingest` 会复用相同视频、时间范围、prompt、转录和抽帧设置的既有 sheet，但不会按空白、近重复内容或重复 frame index 折叠采样帧；视频剪辑任务需要尽量完整地看到固定采样覆盖到的画面。`video_watch_segment` 会按源视频路径、大小和修改时间生成独立窗口台账，按 `(start, end, fps)` 跳过同一源视频的精确重复复看窗口（换 fps 重看不算重复，`force=true` 可强制重看）；被跳过的重复窗口不会产生新的 contact sheet，工具会返回 `skipped_duplicate` 状态，且不会把这次调用当成新的视觉观察来更新 active video。重叠但不精确相同的窗口会提示已覆盖范围；这个台账只管理时间窗，不折叠帧内容。active video 也使用同一类指纹保护，避免源文件被覆盖后继续沿用旧 transcript 或旧全片观察结论。

抽帧读取视频元数据时优先使用 OpenCV / python_vali；ffprobe 兜底路径会同时读取 stream duration 和 format duration，避免只有容器时长或只有流时长的视频无法采样。

## Timeline 项目格式

见 `examples/timeline_minimal.json`、`examples/timeline_project_rich.json` 和 `schemas/timeline.schema.json`。Pipeline 输出的 timeline 根节点必须是 JSON object，并且必须包含非空 `project`、`assets[]`、`tracks[]`，还必须包含 `sequence` 或 `output_canvas`。顶层 `clips[]` 只保留给 legacy/debug 兼容：`validate_timeline` 以 legacy 模式校验它（契约缺失降级为 warning，`mode: "legacy"`），`render_preview` 能渲染它，但 pipeline 任务的完成门仍要求项目式 `tracks[]`。track 和 clip 都必须是 object；每个 clip 必须有 `start`、`reason`，并且必须提供 `end` 或 `duration`。视频 / main 轨的 clip 必须有 `source`；`tracks[]` 中非视频轨的 clip 可以用非空 `text` 代替 `source` 来记录字幕 / overlay 计划。如果同时提供 `end` 和 `duration`，`duration` 必须与 `end - start` 一致。所有时间值必须是有限数字，不能是 `NaN`、`Infinity` 或布尔值。

推荐新任务尽量写成项目文件，而不是只有裸 `clips[]`：`project` 记录任务和假设，`sequence` / `output_canvas` 记录分辨率、fps、时基和平台目标，`assets[]` 记录源素材清单，`tracks[]` 表达主视频、BGM、旁白、字幕、overlay、callout 等轨道计划，`markers[]` 记录关键节拍，`transitions[]` 或 clip 级 `transition_in/out` 记录转场计划，clip / track 可带 `effects[]`、`timeline_start`、`volume`、`opacity`、`enabled` 等编辑软件常见元数据。Validation 会校验这些字段的基本类型、数值边界、资产引用、轨内重叠和 sequence 时长边界。

`render_preview` 现在是基础项目渲染器：`tracks[]` 中父级 track 的 `type/name/track_type` 明确为 `video` / `main` / `primary` / `footage` 等主视频含义时，会按 `timeline_start` 放到 sequence 画布上并在片尾补少量末帧以降低边界闪帧；原视频音频与 `audio` / `music` / `voiceover` 等音频轨会先烘焙成全片长度、已按时间线定位的 wav bed，再用非归一化混音合成；`subtitle` / `text` 等文字轨会通过 `drawtext` 烧录；`overlay` / `image` 轨会按启用时间窗叠加静态图片。渲染后会额外写 `preview.render_plan.json` 和 `preview.edit_decisions.json`，方便检查 timeline 如何被编译成输出。**这三份 sidecar 是项目式渲染的产物；legacy `clips[]` 走线性拼接分支，只写 `preview.render_report.json`**。

这个 renderer 仍不是完整 NLE：复杂关键帧、遮罩、任意转场曲线、嵌套序列、复杂调色、动态贴纸和多层高级合成仍应作为项目计划记录，不能声称已完整渲染。未知 track 类型会记录到 render plan 的 unsupported tracks 中。

```json
{
  "version": "1.0",
  "clips": [
    {
      "source": "input.mp4",
      "start": 0.0,
      "end": 5.0,
      "reason": "开场信息完整"
    }
  ]
}
```

`timeline_diff` 对 `clips[]` 和 `tracks[].clips[]` 都会原地修改对应 clip 容器，不会为了修复 tracks timeline 而额外生成顶层 `clips`。`patch` 必须是 JSON object，`apply` 必须是 boolean。可用操作包括：`set_timeline_fields`、`set_track_fields`、`add_assets`、`update_assets`、`remove_asset_indices`、`add_tracks`、`insert_tracks`、`replace_tracks`、`remove_track_indices`、`update_clips`、`replace_clips`、`remove_clip_indices`、`move_clips`、`insert_clips`、`add_clips`。`replace/update/remove/move` 索引按补丁前的扁平 clip 顺序解释；asset/track 操作用各自数组内的索引解释；结构性 track 操作（add/insert/replace/remove tracks）会改变 clip 容器，不能和 clip 操作混在同一个 patch，必须拆成两次 `timeline_diff`。`move_clips` 每次 patch 最多一条，移动已有 clip，随后才执行 `insert_clips` / `add_clips`；多次重排必须拆成多次 `timeline_diff`。对 `tracks[]` timeline 使用 `add_clips` 时，会优先加入第一个视频 / main 轨；`insert_clips` 和 `move_clips` 可传 `track_index` 指定目标轨道。重复索引、越界索引、非对象 clip/fields、空 `apply=true` patch、多条 move、结构性 track 操作和 clip 操作混用都会被校验拒绝。
