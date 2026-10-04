<h1 align="center">XiaoYunQue</h1>

<p align="center">
  <b>An AI directing agent that turns a single idea into a finished short film</b>
</p>

<p align="center">
  <a href="./README.md">简体中文</a> | <b>English</b>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-purple.svg" alt="Python">
  <img src="https://img.shields.io/badge/Node.js-18+-green.svg" alt="Node">
  <img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License">
  <img src="https://img.shields.io/badge/by-Innate%20Labs-ff4444.svg" alt="Innate Labs">
</p>

---

## 📖 Overview

**XiaoYunQue** is an AI directing system for short-video / short-drama creation. Give it a single sentence, a synopsis, or even a vague concept, and it decomposes the idea into an executable film-production pipeline — continuously producing **viewable, confirmable, editable, and resumable** intermediate assets, and finally delivering a complete film.

It is not a single-shot "text-to-video" black box, but a full pipeline:

```
Script → Character/Scene Design → Storyboard → Reference Images → Video Generation → Post-production
```

Each stage is handled by a dedicated agent; the output of one stage drives the next, and every key checkpoint is visualizable, editable, and resumable after changes — more like a **collaborative AI directing team** than a one-shot tool.

---

## ✨ Features

| Capability | Description |
| --- | --- |
| 🎬 **End-to-end generation** | One pipeline connecting script, characters, storyboard, reference images, video clips and post-production. |
| 🖼️ **Storyboard-driven control** | Structured scripts, storyboard planning and reference images keep character consistency, shot language and visual style stable and controllable. |
| ✍️ **Editable, continuable, resumable** | Smart continuation of plot/storyboard, and regeneration after edits at the character / reference / video stages. |
| 🧩 **Pluggable models** | LLM / VLM / image / video stages can switch across providers (DashScope, Volcengine ARK, Kling, OpenAI, Gemini, DeepSeek, …). |
| 📲 **Local deployment & asset retention** | Backend + web frontend run locally, with full retention of scripts, images, clips and the final film. |

### 🎥 Three video generation modes

- **First-frame to video** (recommended, most stable): start from the stage-4 first-frame reference image plus the storyboard prompt.
- **First-and-last-frame to video**: use the current clip's reference as the first frame and the next clip's reference as the last frame for stronger transitions.
- **Reference-to-video**: feed character/scene images directly as references to emphasize identity/scene consistency.

---

## 🏗️ Architecture

XiaoYunQue uses a **single orchestrator + multi-stage agents** design: one orchestrator drives a state machine, dispatching dedicated agents across 6 stages, each persisting artifacts and waiting for user confirmation.

```
┌─────────────┐     ┌──────────────────────────────────────────────┐
│  Frontend    │────▶│  Orchestrator (6-stage state machine)          │
│ (Next.js)   │◀────│   ├─ ScriptWriterAgent       Script            │
└─────────────┘     │   ├─ CharacterDesignerAgent  Character/Scene   │
                    │   ├─ StoryboardAgent          Storyboard        │
┌─────────────┐     │   ├─ ReferenceGeneratorAgent  Reference images  │
│ Model layer  │◀───│   ├─ VideoDirectorAgent       Video generation  │
│ LLM/VLM/img/v│     │   └─ VideoEditorAgent         Post-production   │
└─────────────┘     └──────────────────────────────────────────────┘
```

- **Backend**: Python + FastAPI. `orchestrator.py` manages the 6-stage state machine and session state; each `*_agent.py` implements one stage; `models/` wraps the LLM / VLM / image / video clients.
- **Frontend**: Next.js + React — creation workspace, stage confirmation, configuration and asset preview.
- Backend defaults to `http://localhost:8000`, frontend to `http://localhost:3000`.

---

## 🚀 Quick Start

### Requirements

- **Python 3.10+** (3.12 recommended; some code uses 3.10+ syntax and will not run on 3.9)
- **Node.js 18+** / **npm 9+**
- **ffmpeg** (for video concatenation and A/V post-processing)
- [`uv`](https://docs.astral.sh/uv/) recommended for the backend environment

### Option 1: One-click install (recommended)

```bash
git clone https://github.com/Innate-Labs/xiaoyunque.git
cd xiaoyunque
cd xiaoyunque/xiaoyunque
chmod +x install.sh
./install.sh
```

The installer checks Python / Node.js / npm / ffmpeg, installs backend and frontend dependencies, and copies `backend/config.yaml.example` to `backend/config.yaml`.

Skip the frontend build temporarily:

```bash
XYQ_SKIP_FRONTEND_BUILD=1 ./install.sh
```

### Option 2: Manual install

```bash
# Backend
cd xiaoyunque/xiaoyunque/backend
uv sync --python 3.12          # or: python -m venv venv && pip install -r requirements.txt
cp config.yaml.example config.yaml   # then fill in API keys & confirm default models

# Frontend (new terminal)
cd xiaoyunque/xiaoyunque/frontend
npm install
```

### Run

```bash
# Backend
cd xiaoyunque/xiaoyunque/backend
uv run python api_server.py            # http://localhost:8000

# Frontend (new terminal)
cd xiaoyunque/xiaoyunque/frontend
npm run dev                            # dev mode; or npm run build && npm start
```

Open **http://localhost:3000** and type your idea to start. API keys and default models can also be set from the frontend **Settings** page.

---

## 🔧 Configuration

Backend config lives in `xiaoyunque/xiaoyunque/backend/config.yaml` (lowercase, hierarchical YAML). Edit it directly or via the frontend **Settings** page.

- `api_providers`: keys, base URLs and proxy switches per provider.
- `models`: default models for the main pipeline (no implicit fallback — missing models raise an error).
- `generation`: default style, ratio, resolution and video generation mode.

> ⚠️ `config.yaml` is git-ignored — put real keys here and do not commit them.

### Key ↔ provider mapping

| Provider | Field | Typical use |
| :--- | :--- | :--- |
| **DashScope** | `dashscope.api_key` | Qwen text, Wan image/video |
| **Volcengine ARK** | `ark.api_key` | Doubao Seedream images, Seedance video |
| **Kling** | `kling.access_key` / `secret_key` | Kling video |
| **OpenAI** | `openai.api_key` | GPT text/vision, OpenAI images |
| **Gemini** | `gemini.api_key` | Gemini text/vision |
| **DeepSeek** | `deepseek.api_key` | DeepSeek text |

Only fill in the providers for the models you actually use. The available-model list is defined in `backend/models/config_model.py`.

---

## 📁 Outputs

All metadata and generated assets are stored under `xiaoyunque/xiaoyunque/backend/code/`:

```text
backend/code/
├── data/sessions/        # session metadata (JSON)
└── result/
    ├── image/<session>/  # character/scene assets + storyboard reference images
    ├── video/<session>/  # generated video clips
    └── script/           # script / storyboard data
```

---

## 🗺️ Roadmap

XiaoYunQue is evolving toward true multi-agent collaboration:

- [ ] Role-level agents that review each other (screenwriter / director / continuity, each with its own loop & memory)
- [ ] A dedicated cross-shot continuity agent (character/scene consistency, able to reject & redo)
- [ ] Concurrent storyboard agents + aggregation (parallel multi-shot generation)

---

## 🙏 Acknowledgements

This project is built on the open-source [FilmAgent / Video-Claw](https://github.com/HITsz-TMG/FilmAgent) (MIT License). Thanks to the original authors.

## 📄 License

[MIT](./LICENSE) © 2026 Innate Labs (original upstream copyright retained).
