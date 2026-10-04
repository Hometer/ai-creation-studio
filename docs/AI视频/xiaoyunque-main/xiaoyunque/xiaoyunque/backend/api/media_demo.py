"""Persistent artificial media samples for local acceptance, with no AI routes."""

import argparse
import subprocess
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from PIL import Image
from pydantic import BaseModel

from api.dependencies import get_workflow_engine
from api.errors import ErrorResponse, HTTP_ERRORS, register_error_handlers
from api.routers.assets import get_asset_library, get_media_archive, router
from api.routers.generation_plans import router as plans_router
from api.routers.generation_tasks import get_task_provider, router as tasks_router
from core.asset_library import AssetLibrary
from core.media_archive import MediaArchive
from core.orchestrator import WorkflowEngine
from core.task_provider import LocalSimulationProvider


class DemoInfo(BaseModel):
    service: str
    project_id: str
    docs: str


def create_demo(directory: Path, task_simulation: bool = False):
    image_dir = directory / "code/result/image/media-demo"
    video_dir = directory / "code/result/video/media-demo"
    image_dir.mkdir(parents=True, exist_ok=True)
    video_dir.mkdir(parents=True, exist_ok=True)
    images = [image_dir / "yellow.png", image_dir / "blue.png"]
    for path, color in zip(images, ("gold", "royalblue")):
        if not path.exists():
            Image.new("RGB", (640, 360), color).save(path)
    video = video_dir / "sample.mp4"
    if not video.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=gold:s=320x180:r=10",
                        "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(video)],
                       capture_output=True, check=True, timeout=10)
    engine = WorkflowEngine(str(directory / "sessions"))
    if engine.get_status_snapshot("media-demo") is None:
        engine.create_session("media-demo", {"idea": "本地人工色块验收，不代表模型结果"})
        paths = [str(path.relative_to(directory)) for path in images]
        engine.get_state("media-demo").artifacts = {
            "character_design": {"characters": [{"id": "role-1", "name": "人工色块图片",
                "description": "第一版黄色，第二版蓝色，均由本地程序制作",
                "selected": paths[0], "versions": paths}]},
            "video_generation": {"clips": [{"id": "seg-1", "name": "人工色块 MP4",
                "description": "ffmpeg 本地制作，非模型结果", "selected": str(video.relative_to(directory)),
                "versions": [str(video.relative_to(directory))]}]},
            "storyboard": {"episodes": [{"episode_number": 1, "segments": [
                {"segment_id": "seg-1", "shots": [{"shot_number": 1, "duration": 5,
                    "content": "人工黄色色块缓慢变成蓝色；只验证输入准备，不调用模型"}]}]}]},
        }
        engine.save_session_to_disk("media-demo")
    library = AssetLibrary(directory / "asset-library")
    archive = MediaArchive(library.directory, directory / "code/result", directory)
    title = "小云雀任务与费用 · 人工演练（SIM 点数，无模型调用）" if task_simulation else "小云雀媒体归档 · 人工样例验收"
    app = FastAPI(title=title, responses={status: {"model": ErrorResponse} for status in HTTP_ERRORS})
    register_error_handlers(app)
    app.include_router(router)
    app.include_router(plans_router)
    app.include_router(tasks_router)
    app.dependency_overrides[get_workflow_engine] = lambda: engine
    app.dependency_overrides[get_asset_library] = lambda: library
    app.dependency_overrides[get_media_archive] = lambda: archive
    if task_simulation:
        provider = LocalSimulationProvider(directory / "simulation-provider", video)
        app.dependency_overrides[get_task_provider] = lambda: provider

    @app.get("/", response_model=DemoInfo)
    def info():
        return {"service": "人工样例验收；没有模型调用", "project_id": "media-demo", "docs": "/docs"}

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--task-simulation", action="store_true", help="仅启用本地人工平台与 SIM 点数，不调用模型")
    arguments = parser.parse_args()
    try:
        app = create_demo(arguments.directory.resolve(), task_simulation=arguments.task_simulation)
    except (OSError, ValueError, subprocess.SubprocessError):
        print("人工样例初始化失败，请保留数据并检查 ffmpeg 与目录权限。", file=sys.stderr)
        return 1
    print(f"人工样例验收：http://127.0.0.1:{arguments.port}/docs；无模型调用，验收记录在独立 data 目录。", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=arguments.port, access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
