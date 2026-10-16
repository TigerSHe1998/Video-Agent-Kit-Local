# Video Agent Kit Local

让 [ZCode](https://z.ai) 变成视频剪辑助手的插件：你用一句话描述需求，AI 读取你的素材、听懂语音、看懂画面，自动完成转录、剪辑、字幕、渲染和质检，产出成片与可审计的报告。

- **本地语音转录** —— 对接 [Moss Transcribe WebUI](https://github.com/TigerSHe1998/Moss-Transcribe-WebUI)，在你的 GPU 上运行，无需任何云端凭据
- **看懂画面** —— 视频自动抽帧成带时间戳的 contact sheet，AI 逐帧阅读后再做剪辑决策
- **完整剪辑管线** —— 时间线校验 / 渲染 / 质量检查（黑帧、静音、冻结帧、响度） / 可审计修复
- **字幕与口播压缩** —— 中文断句排版、烧录、说话人标注；长口播视频自动去水浓缩
- **全自动环境管理** —— Python 依赖装在插件自带的 venv 里，首次使用自动配置，不污染全局

> 仅支持 Windows。

## 环境要求

| 依赖 | 说明 |
|---|---|
| [ZCode](https://z.ai) | 插件宿主 |
| Python 3.10+ | 在 PATH 上能以 `python3` 调用（[python.org](https://www.python.org/downloads/) 安装时勾选 *Add to PATH*，并开启 `py --list` 中的 App Execution Alias 或自行建立 `python3` 别名） |
| FFmpeg | 需带 `libx264` / `aac` / `libmp3lame` / `libass`（推荐 [gyan.dev full 构建](https://www.gyan.dev/ffmpeg/builds/)，`winget install --id Gyan.FFmpeg -e` 即可） |
| [Moss Transcribe WebUI](https://github.com/TigerSHe1998/Moss-Transcribe-WebUI) | 本地转录服务，部署好后把它的 `cli/` 目录加入 PATH（使 `transcribe` 命令可用） |

三项都装好了吗？可以先跳到 [验证环境](#验证环境) 一键体检。

## 安装

### 1. 克隆仓库

```bash
git clone https://github.com/TigerSHe1998/Video-Agent-Kit-Local.git
```

记住克隆到的路径，下文以 `D:\Code\Video-Agent-Kit-Local` 为例。

### 2. 在 ZCode 中添加本地市场

1. 打开 ZCode → **插件市场 / Plugin Marketplace**
2. 点击 **添加 / Add → 添加插件市场 / Add Plugin Marketplace**
3. 粘贴仓库根目录路径（如 `D:\Code\Video-Agent-Kit-Local`），点击添加

仓库根目录自带 `marketplace.json`，ZCode 会识别它为一个本地插件市场。

### 3. 安装插件

1. 进入 **个人 / Personal**，找到 **dev 市场 → 视频剪辑工具包（本地版）**
2. 点击 **安装 / Install**

### 4. 开始使用

**新建一个任务**，打开（或切换到）装有视频素材的工作区文件夹，直接说需求，例如：

```
帮我把这个视频里的精彩语音部分剪成一个 30 秒以内的精华版，加上中文字幕，
输出到 out/preview.mp4，并给我质检报告。
```

AI 会自动完成：素材探查 → 语音转录 → 逐帧看片 → 生成时间线 → 渲染 → 质检 → 修复循环，最后在 `out/` 目录产出成片和报告。

也可以输入 `/video-edit <任务描述>` 显式触发剪辑流程。

> **首次调用会有 1~2 分钟等待**：插件正在自动创建自己的 Python 虚拟环境并安装依赖（装在插件目录内的 `.venv`，不碰全局）。之后就是秒级启动。

## 验证环境

安装后可随时体检系统依赖（FFmpeg 编码器/滤镜、中文字体、转录 CLI、磁盘空间）：

```
/env-check
```

在 ZCode 任务中输入即可。体检会逐项报告缺什么、影响什么能力、怎么装，结论落在 `.video_agent/env_report.json`。

## 常见问题

**Q: Python 依赖会装到哪里？**
插件目录内的 `.venv\`。首次调用任何剪辑工具时自动创建；插件更新时重建；卸载插件时随目录一起删除，全程不污染全局 Python。

**Q: 打开工作区就出现了 `out\` 和 `.video_agent\` 文件夹？**
正常。它们只在第一次实际使用剪辑工具后出现：`out\` 是成片和报告，`.video_agent\` 是中间产物，都可以随时删除。

**Q: 转录用的是模型还是云端？**
纯本地。转录请求发给本机的 Moss Transcribe WebUI 服务（默认 `127.0.0.1:8390`），没有服务时 `transcribe` CLI 会自动拉起一个。转录大文件不会复制素材（零拷贝）。

**Q: 支持多大的视频？**
单文件上传上限 32 GB；超过 30 分钟的音频自动分段转录再合并时间戳。200 GB 级的多素材项目可正常工作。

**Q: 怎么更新插件？**

```bash
cd <仓库路径>
git pull
```

然后在 ZCode 的 **市场源 / Marketplace Sources** 里刷新该市场，再到插件详情页点 **更新 / Update**。

## 更多文档

- [Agent 参考手册](docs/AGENT_REFERENCE.md) —— 23 个 MCP 工具的完整契约、文件产出约定、时间线格式、配置项与 CLI 用法（面向 AI Agent 与深度使用者）
- [Moss Transcribe WebUI](https://github.com/TigerSHe1998/Moss-Transcribe-WebUI) —— 本地转录服务的部署与使用

## License

MIT
