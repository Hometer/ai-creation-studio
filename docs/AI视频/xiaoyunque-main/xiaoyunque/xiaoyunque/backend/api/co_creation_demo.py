"""Isolated human intent records with artificial text and no model routes."""
import argparse
from pathlib import Path
import uvicorn
from api.comparison_demo import create_comparison_demo
from api.routers.co_creation import router

def create_co_creation_demo(directory: Path):
    app = create_comparison_demo(directory)
    app.title = '对话共创意图 · 人工样例，不调用模型'
    app.include_router(router)
    return app

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8009)
    args = parser.parse_args()
    uvicorn.run(create_co_creation_demo(args.directory.resolve()), host='127.0.0.1', port=args.port, access_log=False)

if __name__ == '__main__':
    main()
