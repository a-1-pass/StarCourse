<p align="center">
  <img src="docs/images/logo.png" alt="StarCourse Logo" width="120">
</p>

<h1 align="center">🌟 StarCourse</h1>

<p align="center">
  <strong>学习通（超星）自动刷课桌面客户端</strong> —— PyQt6 图形界面 · 纯 HTTP 引擎 · 多 AI 供应商答题
</p>

<p align="center">
  <a href="https://github.com/a-1-pass/StarCourse/releases"><img src="https://img.shields.io/github/v/release/a-1-pass/StarCourse?display_name=tag&sort=semver&label=release" alt="release"></a>
  <a href="https://github.com/a-1-pass/StarCourse/blob/main/LICENSE"><img src="https://img.shields.io/github/license/a-1-pass/StarCourse" alt="license"></a>
  <img src="https://img.shields.io/badge/python-%3E%3D3.11-blue" alt="python">
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey" alt="platform">
  <a href="https://github.com/a-1-pass/StarCourse/stargazers"><img src="https://img.shields.io/github/stars/a-1-pass/StarCourse?style=social" alt="stars"></a>
</p>

<p align="center">
  ⭐ 如果这个项目帮到了你，点右上角 <strong>Star</strong> 支持一下，这是开源持续更新最大的动力！
</p>

---

## 📥 下载即用（3 步上手）

| 平台 | 下载方式 | 大小 | 说明 |
|------|---------|------|------|
| **Windows** | [Releases](https://github.com/a-1-pass/StarCourse/releases) 下载 `StarCourse-windows-x64.zip` | ~46 MB | 解压后双击 `StarCourse.exe`，无需安装 |
| **macOS** | [Releases](https://github.com/a-1-pass/StarCourse/releases) 下载 `StarCourse-macos-arm64.zip` | ~48 MB | 解压后运行；如被 Gatekeeper 拦截 → 右键 → 打开 |
| **Linux** | [Releases](https://github.com/a-1-pass/StarCourse/releases) 下载 `StarCourse-linux-x64.zip` | ~50 MB | 解压后 `./StarCourse` 运行 |

**使用流程：**
1. **下载解压** → 双击运行（首次启动自动创建配置，无需手动改文件）
2. **添加账号** → 弹窗输入手机号/学号 + 密码（信息仅保存在本地）
3. **选课开刷** → 选中课程和章节，点「开始执行」

> 💡 **首次启动向导**：软件检测到没有保存过账号时，会自动弹出引导窗口，帮你完成初始设置。所有参数均已填好安全默认值，可直接「下一步」跳过。

---

## ⚠️ 用前必读

- 本项目**仅供学习与技术交流**，请勿用于任何违反校规、平台服务条款或法律法规的用途。
- 使用自动化工具操作账号存在被平台风控、限制甚至封禁的风险，**账号风险由使用者自行承担**。
- 建议控制使用频率：不要同时开启过多线程、不要在短时间内高频请求，倍速建议不超过 2.0。
- **不支持考试**——一键完成的范围不包括考试与测试（详见 FAQ）。
- 所有功能均通过发送 GET/POST 请求实现，无需浏览器，效率高、占用低，但平台接口随时可能更新导致部分功能失效，请留意更新。

---

## 🖥️ 界面预览

| 首次启动向导 | 账号面板 |
| :---: | :---: |
| <img src="docs/screenshots/0-首次向导.png" alt="首次启动向导"> | <img src="docs/screenshots/1-账号面板.png" alt="账号面板"> |
| 三步引导：欢迎 → 添加账号 → 运行参数 | 多账号列表：用户名 / 显示名称 / 状态 / 目标课程，批量运行 |

| 课程工坊（章节定点） | 签到面板 |
| :---: | :---: |
| <img src="docs/screenshots/2-课程工坊.png" alt="课程工坊"> | <img src="docs/screenshots/3-签到面板.png" alt="签到面板"> |
| 章节树勾选定点刷课 + 学习模式选择（闯关/顺序/并发/复习） | 五种签到类型（普通/手势/位置/二维码/拍照）+ 一键全部签到 |

| AI 设置与答题日志 |
| :---: |
| <img src="docs/screenshots/4-AI设置.png" alt="AI 设置"> |
| 多 AI 供应商配置（容灾链）与运行日志导出 |

> 截图为占位路径，图片文件尚未提交，见 [docs/release-checklist.md](docs/release-checklist.md) 中的截图拍摄指引。

---

## ✨ 功能亮点

### 🏆 同类工具几乎全没有的能力

1. **桌面 GUI + 多账号管理**
   账号面板统一管理多个学习通账号，每个账号独立工作台窗口互不干扰；Cookie 免密登录，过期后自动回退密码登录。同类竞品几乎全是单账号命令行。

2. **五种签到一键全签**（全场独有）
   普通 / 二维码 / 手势 / 位置 / 拍照，一键扫描全部课程的待签到活动并批量完成。

3. **章节级定点刷课**
   GUI 勾选指定章节，只刷你选的部分——竞品最多到课程级。

4. **首次启动零配置**
   下载解压即运行，无需编辑 config.yml，无需配置环境变量，新手 30 秒上手。

### 🧠 刷课与答题引擎

5. **双层断点续学 + 四种学习模式**
   章节级 SQLite 进度记忆（重启接着刷）+ 单视频 playTime 续看；闯关 / 顺序 / 并发 / 复习四种模式可选。

6. **答题全家桶**
   - 三家第三方题库（言溪 / LIKE / TikuGo）+ TikuAdapter 自建题库接入，多 provider 轮询回退；
   - AI 答题支持 **DeepSeek / OpenAI / Claude / 文心一言 / 阶跃星辰**，priority 链自动容灾切换；
   - **客观题穷举引擎**：应对「正确率过低打回重做」，贪心爬坡 + Hamming 枚举反复提交直到整卷通过（默认关闭）。

7. **字体反混淆 + 图片题视觉 OCR**（默认关闭，按需开启）
   - 解析 `font-cxsecret` 动态混淆字体还原题干，fontTools 字形哈希 + 附 `tools/build_font_map.py` 映射表生成工具；
   - 题干/选项是图片的题目，走多模态大模型视觉识别为文本后再作答。

### 🛠️ 细节与工程化

8. **任务类型全覆盖**：视频/音频、文档/PPT、阅读、电子书、讨论区回帖、直播回放、超链接、章节测验、课后作业（**不含考试**）。
9. **资源/附件下载**：扫描课程内媒体文件与课件资料，批量下载到指定目录。
10. **倍速（1.0–2.0）、即时完成、多线程并发**（线程数可调）。
11. **反检测**：UA 池 + Client Hints 指纹头 + 请求抖动 + 限流退避。
12. **日志轮转 + 一键导出 zip**：反馈问题时一键打包日志。
13. **质量保障**：265 个 pytest 测试用例护航。

---

## 🚀 开发者快速开始（源码运行）

如果你希望从源码运行或参与开发：

```bash
git clone https://github.com/a-1-pass/StarCourse.git
cd StarCourse
pip install -r requirements.txt
py main.py          # Windows；macOS/Linux 用 python3 main.py
```

> 无图形界面 / 服务器环境：使用 CLI 模式 `python engine/cli_entry.py`（交互式命令行，配置走 `engine/config.yml`）。

---

## ⚙️ 配置说明（`engine/config.yml`）

| 字段 | 含义 | 默认值 | 可选值 / 说明 |
| --- | --- | --- | --- |
| `FunConfig.deal-mission.video-mode` | 视频完成方式 | `1` | `0`=立即完成，`1`=等时长刷取 |
| `FunConfig.deal-mission.speed` | 视频倍速 | `1.0` | 1.0–2.0，建议勿超 2.0 |
| `FunConfig.deal-mission.enable-multi-thread` | 多线程并发刷取 | `true` | 配合 `max-concurrent-threads`（默认 5） |
| `FunConfig.deal-mission.learning-mode` | 学习模式 | `stage` | `stage`闯关 / `sequential`顺序 / `concurrent`并发 / `review`复习 |
| `FunConfig.deal-mission.progress-persistence` | 章节级进度记忆（断点续学） | `true` | `false` 关闭 |
| `FunConfig.deal-mission.exhaustive.enable` | 客观题穷举引擎 | `false` | 开启后应对正确率打回，注意 `interval` 等风控参数 |
| `FunConfig.deal-mission.font-decode.enable` | 字体反混淆 | `false` | 开启前先运行 `tools/build_font_map.py` 生成映射表 |
| `FunConfig.deal-mission.vision-ocr.enable` | 视觉模型 OCR（图片题） | `false` | 需 AIConfig 中至少一家 OpenAI 兼容多模态提供商可用 |
| `TikuConfig.provider` | 题库提供商 | `''` | `TikuYanxi` / `TikuGo` / `TikuLike` / `TikuAdapter` / `AI` / `SiliconFlow`，逗号分隔可多题库轮询回退 |
| `TikuConfig.token` | 题库 token | `''` | 部分题库必填 |
| `TikuConfig.tikuadapter_url` | TikuAdapter 自建题库地址 | `''` | 如 `http://localhost:8060` |
| `AIConfig.active` / `priority` | AI 默认提供商与容灾链 | `deepseek` 起 | 支持 deepseek / openai / claude / wenxin；GUI「AI 模型设置」还支持阶跃星辰 |
| `AIConfig.providers.*.api_key` | 各厂商 API Key | `''` | 请从各厂商控制台自行申请 |
| `SignConfig.enable` / `check_interval` | 自动签到开关与检查间隔 | `true` / 60 秒 | |
| `GloConfig.delay` | 全局请求延迟（防反爬） | 0.5 秒 | |

> **优先级提示**：GUI 内「AI 模型设置…」保存的 `config.json` 优先于 `config.yml` 中的 `AIConfig` 段；穷举 / 字体反混淆 / 视觉 OCR 等新功能**全部默认关闭**，按需手动开启。

---

## 🗂️ 目录速览

```
StarCourse/
├── main.py              # GUI 入口（PyQt6 主窗口）
├── gui/                 # 图形界面层
│   ├── account_panel.py    # 账号管理面板（多账号列表、批量运行）
│   └── course_workshop.py  # 课程工作台（章节选择/任务队列/签到/下载/AI设置）
├── src/                 # GUI 与引擎的胶水层
│   ├── first_run.py        # 首次启动引导向导（零配置 onboarding）
│   ├── runtime_settings.py # 配置读写（支持自动创建默认配置）
│   ├── account_store.py    # 账号持久化（本地 JSON）
│   └── ...
├── engine/              # 刷课引擎（纯 HTTP，无浏览器依赖）
│   ├── cli_entry.py        # CLI 模式入口（Docker 同用）
│   ├── config.yml          # 主配置文件（首次启动自动创建）
│   ├── tiku.py             # 题库对接
│   ├── ai_providers/       # 多 AI 供应商适配
│   ├── exhaustive.py       # 客观题穷举引擎
│   ├── font_decode/        # 字体反混淆
│   ├── vision_ocr.py       # 视觉模型 OCR
│   └── course_progress.py  # 章节级进度记忆
├── tools/build_font_map.py # 字体映射表生成工具
├── tests/               # 265 个 pytest 用例
├── docs/                # 文档
├── build.bat / build.sh # Windows / macOS 一键打包脚本
├── StarCourse.spec      # PyInstaller 打包配置
└── Dockerfile           # CLI 模式镜像
```

---

## ❓ FAQ

**Q1：会不会封号？安全吗？**
任何自动化工具都无法保证绝对安全。项目内置 UA 池、Client Hints 指纹头、请求抖动与限流退避等反检测措施，但**账号风险需自行承担**。建议：倍速不超过 2.0、并发线程别开太高、避免深夜批量跑。密码输入默认隐藏显示。

**Q2：支持考试吗？**
**不支持。** 一键完成范围不含考试与测试。支持的任务类型：视频/音频、文档/PPT、阅读、电子书、讨论区回帖、直播回放、超链接、章节测验、课后作业。

**Q3：图片题（题干/选项是图片）怎么做？**
开启 `config.yml` 中 `FunConfig.deal-mission.vision-ocr.enable`，并确保 `AIConfig` 里配置了支持视觉的多模态模型（如 OpenAI 的 `gpt-4o`）。图片会先经多模态大模型识别为文本再作答。默认关闭。

**Q4：题目显示乱码（字体混淆）怎么办？**
学习通部分题目用 `font-cxsecret` 动态混淆字体渲染。开启 `font-decode.enable` 前，先运行 `tools/build_font_map.py` 生成字形映射表（`data/font_map_table.json`）；映射表缺失时会自动降级返回原文，不会报错。

**Q5：AI 的 API Key 配在哪？**
两处任选：① GUI 工作台 →「AI 模型设置…」，图形化配置 DeepSeek / OpenAI / Claude / 文心 / 阶跃星辰的 Key、模型与容灾链，写入 `config.json` 即时生效；② 直接编辑 `engine/config.yml` 的 `AIConfig` 段。前者优先。

**Q6：macOS 能用吗？**
能。Releases 提供 macOS arm64 打包版；也可源码运行（Python 3.11+）。首次运行若被 Gatekeeper 拦截，右键应用图标选「打开」即可。CLI 模式同样支持，还有官方 Dockerfile。

**Q7：题库 token 哪里来？**
各题库服务商（言溪 / LIKE / TikuGo）需在对应平台注册获取 token，填到 `TikuConfig.token`；也可以自建 [TikuAdapter](https://github.com/) 题库服务并把地址填到 `tikuadapter_url`。不配题库时可选 AI 答题或本地策略（首选答案/随机选择）。

**Q8：打开后闪退 / 报错怎么办？**
1. 确认是从 [Releases](https://github.com/a-1-pass/StarCourse/releases) 下载的最新版；
2. 删除程序同目录下的 `profiles.json` 后重试；
3. 到 GitHub Issues 反馈，附上 `logs/` 目录中的日志（软件内可点「导出日志」）。

---

## 🔨 源码构建

项目使用 PyInstaller（onefile + windowed）打包单文件程序：

```bash
# Windows
build.bat

# macOS / Linux
bash build.sh
```

两个脚本流程一致：安装 `requirements.txt` → 安装 `pyinstaller>=6.0,<7.0` → 按 `StarCourse.spec` 构建，首次约 5–10 分钟，产物在 `dist/`。推送 `v*` 标签会触发 GitHub Actions（`.github/workflows/release.yml`）自动构建 Windows x64 / macOS arm64 / Linux x64 三平台 zip 并发布 Release，详见 [docs/release-checklist.md](docs/release-checklist.md)。

---

## 🙏 致谢

- [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing) —— 本项目引擎在上游 chaoxing 项目基础上演进而来，感谢先驱者的接口逆向工作。
- 字体反混淆的映射表思路来自开源社区对 `font-cxsecret` 混淆机制的公开研究。
- 感谢言溪 / LIKE / TikuGo 题库与 TikuAdapter 项目提供的答题接口生态。

---

## 📄 许可证

本项目基于 [MIT License](LICENSE) 开源，可自由使用、修改与分发，风险自担。

---

## 📈 Star History

<a href="https://star-history.com/#a-1-pass/StarCourse&Date">
  <img src="https://api.star-history.com/svg?repos=a-1-pass/StarCourse&type=Date" alt="Star History Chart">
</a>

---

<details>
<summary><b>English README (click to expand)</b></summary>

# StarCourse

A desktop automation client for the Chaoxing (超星学习通) online course platform — PyQt6 GUI + pure-HTTP engine (requests only, no Selenium/browser) + multi-provider AI quiz answering.

## Download & Run (3 Steps)

1. Download the zip for your platform from [Releases](https://github.com/a-1-pass/StarCourse/releases)
2. Extract and double-click `StarCourse.exe` (Windows) or `StarCourse` (macOS/Linux)
3. Add your account in the welcome wizard and start brushing courses

No config file editing, no Python installation, no terminal required.

## Highlights

- **Desktop GUI + multi-account management**: account panel, independent workspace window per account, cookie-based password-free login with automatic password fallback. Most competitors are single-account CLI tools.
- **Five sign-in types, one click**: normal / QR-code / gesture / location / photo — scan all courses and sign in batch. Unique among similar tools.
- **Chapter-level targeting**: pick specific chapters in the GUI instead of the whole course.
- **Zero-config first run**: auto-generates default config, shows onboarding wizard for new users.
- **Two-layer resume**: chapter-level SQLite progress memory + per-video playTime resume; four learning modes (stage-gated / sequential / concurrent / review).
- **Answering stack**: three third-party question banks (Yanxi / LIKE / TikuGo) + self-hosted TikuAdapter, multi-provider polling fallback; AI answering via DeepSeek / OpenAI / Claude / Wenxin / StepFun with priority-based failover; optional exhaustive engine for objective questions (default off).
- **Font de-obfuscation** (`font-cxsecret` decoding via fontTools glyph hashing, with `tools/build_font_map.py`) and **vision-LLM OCR** for image-based questions (both default off).
- Task coverage: video/audio, docs/PPT, reading, e-books, discussion posts, live replays, hyperlinks, chapter quizzes, homework. **Exams are NOT supported.**
- Distribution: Windows exe / macOS package / Linux binary via Releases, source run (Python 3.11+, `py main.py`), CLI mode (`engine/cli_entry.py`), and Docker (CLI only).
- Anti-detection: UA pool, Client Hints headers, request jitter, rate-limit backoff. Rotating logs with one-click zip export. 265 pytest cases.

## Quick Start (Source)

```bash
git clone https://github.com/a-1-pass/StarCourse.git
cd StarCourse
pip install -r requirements.txt
py main.py   # or python3 main.py on macOS/Linux
```

Add an account in the GUI, open its workspace, select a course (and optionally chapters), queue it, and hit Start.

## Disclaimer

For learning and technical exchange only. Do not use for any purpose that violates school rules, platform terms of service, or the law. Account risk is your own responsibility; keep request rates moderate.

## License

[MIT](LICENSE)

</details>
