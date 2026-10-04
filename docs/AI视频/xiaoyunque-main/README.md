<h1 align="center">小云雀 XiaoYunQue</h1>

<p align="center">
  <b>从一句创意到一部成片的 AI 短片创作 Agent</b>
</p>

<p align="center">
  <b>简体中文</b> | <a href="./README_EN.md">English</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-purple.svg" alt="Python">
  <img src="https://img.shields.io/badge/Node.js-18+-green.svg" alt="Node">
  <img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License">
  <img src="https://img.shields.io/badge/by-Innate%20Labs-ff4444.svg" alt="Innate Labs">
</p>

---

## 📖 项目介绍

**小云雀（XiaoYunQue）** 是一个面向短视频 / 短剧创作的 AI 导演系统。你只需要给出一句想法、一段梗概，甚至一个模糊的概念，系统就会把它拆解成一条可执行的影视生产流水线，持续产出**可查看、可确认、可修改、可继续生成**的中间资产，最终交付完整成片。

它不是单点式的"文生视频"黑盒，而是一条覆盖全流程的生产线：

```
剧本策划 → 角色/场景设计 → 分镜规划 → 参考图生成 → 视频生成 → 后期剪辑
```

每一个阶段都由一个专职 Agent 负责，前一阶段的产物决定后一阶段的输入；所有关键节点都可视化、可编辑、可在修改后继续生成——更像一个**可协作的 AI 导演团队**，而不是一次性出片的工具。

---

## ✨ 核心特性

| 能力 | 说明 |
| --- | --- |
| 🎬 **从创意到成片的全流程生成** | 一条链路打通剧本、角色、分镜、参考图、视频片段与后期剪辑，把零散的生成能力升级为完整的视频生产工作流。 |
| 🖼️ **分镜驱动的可控创作** | 通过结构化剧本、分镜规划与参考图生成，让角色一致性、镜头表达与画面风格更稳定、更可控。 |
| ✍️ **可修改、可续写、可继续生成** | 支持剧情 / 分镜智能续写，也支持在角色、参考图、视频阶段修改后重新生成，避免每次都从头开始。 |
| 🧩 **多模型可插拔** | LLM / VLM / 图像 / 视频各环节均可在多家服务商间切换（通义、火山方舟、可灵、OpenAI、Gemini、DeepSeek 等）。 |
| 📲 **本地部署、产物留存** | 后端 + Web 前端本地运行，对剧本、图片、视频片段与最终成片做全链路留存。 |

### 🎥 三种视频生成方式

主流程的视频生成阶段支持三种方式，可在生成配置中切换并分别配置模型：

- **首帧生视频**（推荐，最稳定）：以第四阶段生成的首帧参考图为起点，结合分镜提示词生成单个片段。
- **首尾帧生视频**：以当前片段参考图为首帧、下一片段参考图为尾帧，画面衔接更强。
- **参考图生视频**：直接读取角色图与场景图作为参考素材，强调角色 / 场景一致性。

---

## 🏗️ 架构

小云雀采用 **单编排器 + 多阶段 Agent** 的结构：一个 orchestrator 驱动状态机，按 6 个阶段依次调度专职 Agent，每个 Agent 完成后落盘产物并等待用户确认。

```
┌─────────────┐     ┌──────────────────────────────────────────────┐
│  Web 前端    │────▶│  Orchestrator（6 阶段状态机 / 会话持久化）        │
│ (Next.js)   │◀────│   ├─ ScriptWriterAgent     剧本策划             │
└─────────────┘     │   ├─ CharacterDesignerAgent 角色/场景设计        │
                    │   ├─ StoryboardAgent        分镜规划             │
┌─────────────┐     │   ├─ ReferenceGeneratorAgent 参考图生成          │
│  模型服务层   │◀────│   ├─ VideoDirectorAgent      视频生成            │
│ LLM/VLM/图/视│     │   └─ VideoEditorAgent        后期剪辑            │
└─────────────┘     └──────────────────────────────────────────────┘
```

- **后端**：Python + FastAPI，`orchestrator.py` 管理 6 阶段状态机与会话状态，各 `*_agent.py` 实现单阶段逻辑，`models/` 下封装各家 LLM / VLM / 图像 / 视频客户端。
- **前端**：Next.js + React，提供创作工作台、阶段确认、参数配置与产物预览。
- 后端默认 `http://localhost:8000`，前端默认 `http://localhost:3000`。

---

## 🚀 快速开始

### 环境要求

- **Python 3.10+**（推荐 3.12；注意：部分代码使用 3.10+ 语法，3.9 无法运行）
- **Node.js 18+** / **npm 9+**
- **ffmpeg**（视频拼接与音视频后处理需要）
- 推荐使用 [`uv`](https://docs.astral.sh/uv/) 管理后端环境

### 方式一：一键安装（推荐）

```bash
# 1. 克隆项目
git clone https://github.com/Innate-Labs/xiaoyunque.git
cd xiaoyunque

# 2. 进入应用目录执行安装脚本
cd xiaoyunque/xiaoyunque
chmod +x install.sh
./install.sh
```

安装脚本会检查 Python / Node.js / npm / ffmpeg，安装前后端依赖，并把 `backend/config.yaml.example` 复制为 `backend/config.yaml`。

只想装依赖、暂时跳过前端构建：

```bash
XYQ_SKIP_FRONTEND_BUILD=1 ./install.sh
```

### 方式二：手动安装

```bash
# 后端
cd xiaoyunque/xiaoyunque/backend
uv sync --python 3.12          # 没有 uv 时可用 python -m venv venv && pip install -r requirements.txt
cp config.yaml.example config.yaml   # 然后填入 API Key、确认默认模型

# 前端（新终端）
cd xiaoyunque/xiaoyunque/frontend
npm install
```

### 启动

```bash
# 后端
cd xiaoyunque/xiaoyunque/backend
uv run python api_server.py            # http://localhost:8000

# 前端（新终端）
cd xiaoyunque/xiaoyunque/frontend
npm run dev                            # 开发模式；或 npm run build && npm start
```

打开 **http://localhost:3000**，在输入框写下创意即可开始创作。也可在前端「设置」页面填写 API Key 与默认模型，无需手改 YAML。

---

## 🔧 配置说明

后端配置统一保存在 `xiaoyunque/xiaoyunque/backend/config.yaml`（小写、层级化 YAML）。可直接编辑，也可在前端「设置」页面修改。

- `api_providers`：各模型服务平台的密钥、接口地址与代理开关。
- `models`：主流程使用的默认模型；缺少模型参数后端会直接报错，不做隐式兜底。
- `generation`：默认风格、长宽比、分辨率与视频生成方式。

> ⚠️ `config.yaml` 已加入 `.gitignore`，请把真实密钥填在这里，不要提交。

```yaml
project_name: XiaoYunQue

server:
  host: 127.0.0.1
  port: 8000

api_providers:
  openai:    { api_key: '', base_url: https://api.openai.com/v1 }
  gemini:    { api_key: '', base_url: https://generativelanguage.googleapis.com/v1beta }
  deepseek:  { api_key: '', base_url: https://api.deepseek.com/v1 }
  dashscope: { api_key: '', base_url: https://dashscope.aliyuncs.com/api/v1 }
  ark:       { api_key: '', base_url: https://ark.cn-beijing.volces.com/api/v3 }
  kling:     { access_key: '', secret_key: '', base_url: https://api-beijing.klingai.com }

models:
  llm: qwen3.5-plus
  vlm: qwen3.5-plus
  image_t2i: doubao-seedream-5-0-260128
  image_it2i: doubao-seedream-5-0-260128
  video_first_frame: wan2.7-i2v
  video_start_end: wan2.7-i2v
  video_reference: wan2.7-r2v

generation:
  style: realistic
  video_ratio: '16:9'
  video_resolution: 720P
  video_generation_mode: first_frame
```

### 密钥与平台对应关系

| 平台 | 配置字段 | 常用用途 |
| :--- | :--- | :--- |
| **DashScope（通义）** | `dashscope.api_key` | 通义千问、通义万相 Wan 图像/视频 |
| **火山方舟 ARK** | `ark.api_key` | 豆包 Seedream 图像、Seedance 视频 |
| **Kling（可灵）** | `kling.access_key` / `secret_key` | 可灵视频生成 |
| **OpenAI** | `openai.api_key` | GPT 文本/视觉、OpenAI 图像 |
| **Gemini** | `gemini.api_key` | Gemini 文本/视觉 |
| **DeepSeek** | `deepseek.api_key` | DeepSeek 文本 |

只需填写你实际选用模型所对应的平台密钥。例如默认图像模型是 `doubao-seedream-*` 则需配置 `ark.api_key`；默认视频模型是 `wan*` 则需配置 `dashscope.api_key`。可用模型清单以 `backend/models/config_model.py` 为准。

---

## 📁 产物说明

所有任务元数据与生成产物保存在 `xiaoyunque/xiaoyunque/backend/code/`：

```text
backend/code/
├── data/sessions/        # 会话元数据 (JSON)
└── result/
    ├── image/<session>/  # 角色/场景素材 + 分镜参考图
    ├── video/<session>/  # 生成的视频片段
    └── script/           # 剧本/分镜数据
```

- **Session ID**：毫秒级时间戳（如 `1778810088325`），关联主流程上下文与产物。

---

## 🗺️ 路线图

小云雀正在向**真·多 Agent 协作**方向演进：

- [ ] 角色级 Agent 互评（编剧 / 导演 / 连贯性审查各自带 loop 与记忆，互相 review）
- [ ] 独立的跨镜头连贯性 Agent（人物 / 场景一致性审查，可打回重做）
- [ ] 并发分镜 Agent + 汇总（多镜头并行生成）

---

## 🙏 致谢

本项目基于开源项目 [FilmAgent / Video-Claw](https://github.com/HITsz-TMG/FilmAgent)（MIT License）二次开发，在此致谢原作者。

## 📄 License

[MIT](./LICENSE) © 2026 Innate Labs（保留上游原始版权声明）
