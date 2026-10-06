"""Isolated human co-creation draft demo, no model calls."""
import argparse
from pathlib import Path
import uvicorn
from api.co_creation_apply_demo import create_apply_demo

def create_draft_demo(directory: Path):
    app = create_apply_demo(directory)
    app.title = '共创修改草稿 · 人工样例，不调用模型'
    return app

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8011)
    args = parser.parse_args()
    uvicorn.run(create_draft_demo(args.directory.resolve()), host='127.0.0.1', port=args.port, access_log=False)

if __name__ == '__main__': main()
