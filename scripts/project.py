#!/usr/bin/env python3
"""Run the existing XiaoYunQue application from the repository root."""

import argparse
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPLICATION = ROOT / "docs/AI视频/xiaoyunque-main/xiaoyunque/xiaoyunque"
BACKEND = APPLICATION / "backend"
FRONTEND = APPLICATION / "frontend"
PYTHON = BACKEND / ".venv/bin/python"


def run(command: list[str], directory: Path) -> int:
    try:
        return subprocess.run(command, cwd=directory, check=False).returncode
    except FileNotFoundError:
        print(f"缺少命令：{command[0]}，请先运行 doctor。", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def port_available(port: int) -> bool:
    with socket.socket() as connection:
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            connection.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def doctor() -> int:
    failed = False
    for name, command in [
        ("后端 Python（推荐 3.12）", [str(PYTHON), "--version"]),
        ("Node.js（推荐 22）", ["node", "--version"]),
        ("npm", ["npm", "--version"]),
        ("Git", ["git", "--version"]),
        ("ffmpeg", ["ffmpeg", "-version"]),
        ("ffprobe（媒体归档校验）", ["ffprobe", "-version"]),
    ]:
        executable = shutil.which(command[0])
        if not executable:
            print(f"{name}：缺失")
            failed = True
            continue
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        output = (result.stdout or result.stderr).splitlines()
        print(f"{name}：{output[0] if output else '未知版本'}")
        failed = failed or result.returncode != 0
    for name, path in [("后端依赖", PYTHON), ("前端依赖", FRONTEND / "node_modules/.bin/next")]:
        ready = path.exists()
        print(f"{name}：{'已安装' if ready else '缺失'}")
        failed = failed or not ready
    for port in (8000, 3000):
        print(f"端口 {port}：{'可用' if port_available(port) else '占用中，请检查已有服务'}")
    print("本入口不安装依赖、不读取密钥、不调用模型、不停止已有服务。")
    return int(failed)


def check() -> int:
    results = []
    for name, command, directory in [
        ("后端测试", [str(PYTHON), "-m", "pytest", "-q"], BACKEND),
        ("前端回归测试", ["npm", "test"], FRONTEND),
        ("前端类型检查", ["npm", "run", "typecheck"], FRONTEND),
        ("前端 Lint", ["npm", "run", "lint"], FRONTEND),
        ("前端生产构建", ["npm", "run", "build"], FRONTEND),
    ]:
        print(f"开始：{name}", flush=True)
        results.append((name, run(command, directory)))
    for name, result in results:
        print(f"{name}：{'通过' if result == 0 else '未通过'}")
    return int(any(result != 0 for _, result in results))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["doctor", "backend", "frontend", "media-demo", "plan-demo", "task-demo", "task-center-demo", "plan-center-demo", "readiness-center-demo", "revision-demo", "wan-replay", "check"])
    parser.add_argument("--port", type=int, help="服务端口；默认后端 8000、前端 3000、人工媒体验收 8001")
    parser.add_argument("--scenario", choices=["success", "lost-submit", "query-timeout", "bad-video"], default="success")
    parser.add_argument("--action", choices=["step", "status"], default="status", help="万相离线演练：只读状态或推进一步")
    arguments = parser.parse_args()
    if arguments.command == "doctor":
        return doctor()
    if arguments.command == "check":
        return check()
    if arguments.command == "wan-replay":
        return run([str(PYTHON), "-m", "core.wan_replay", "--directory", str(ROOT / "data/wan-protocol-replay"),
                    "--scenario", arguments.scenario, "--action", arguments.action], BACKEND)
    port = arguments.port or {"backend": 8000, "frontend": 3000, "media-demo": 8001, "plan-demo": 8001, "task-demo": 8001, "task-center-demo": 8001, "plan-center-demo": 8001, "readiness-center-demo": 8002, "revision-demo": 8003}[arguments.command]
    if not 1 <= port <= 65535:
        parser.error("端口必须在 1—65535 之间")
    if not port_available(port):
        print(f"端口 {port} 已占用；请使用已有服务或指定 --port。", file=sys.stderr)
        return 1
    if arguments.command == "backend":
        return run([
            str(PYTHON), "-m", "uvicorn", "api.app:app", "--host", "127.0.0.1",
            "--port", str(port), "--no-access-log",
        ], BACKEND)
    if arguments.command == "task-center-demo":
        return run([str(PYTHON), "-m", "api.task_center_demo", "--directory", str(ROOT / "data/task-center-demo"), "--port", str(port)], BACKEND)
    if arguments.command == "plan-center-demo":
        return run([str(PYTHON), "-m", "api.plan_center_demo", "--directory", str(ROOT / "data/plan-center-demo"), "--port", str(port)], BACKEND)
    if arguments.command == "revision-demo":
        return run([str(PYTHON), "-m", "api.revision_demo", "--directory", str(ROOT / "data/revision-demo"), "--port", str(port)], BACKEND)
    if arguments.command == "readiness-center-demo":
        return run([str(PYTHON), "-m", "api.readiness_center_demo", "--directory", str(ROOT / "data/readiness-center-demo"), "--port", str(port)], BACKEND)
    if arguments.command in {"media-demo", "plan-demo", "task-demo"}:
        directory = {"media-demo": "media-archive-demo", "plan-demo": "generation-plan-demo", "task-demo": "generation-task-demo"}[arguments.command]
        return run([str(PYTHON), "-m", "api.media_demo", "--directory", str(ROOT / "data" / directory),
                    "--port", str(port), *(["--task-simulation"] if arguments.command == "task-demo" else [])], BACKEND)
    return run(["npm", "run", "dev", "--", "--hostname", "127.0.0.1", "--port", str(port)], FRONTEND)


if __name__ == "__main__":
    sys.exit(main())
