"""Independent artificial text exports, never provider calls or real user data."""
import argparse
from pathlib import Path
import uvicorn
from api.comparison_demo import create_comparison_demo


def create_export_demo(directory: Path):
    app = create_comparison_demo(directory)
    app.title = '文字版本导出 · 人工样例，不调用模型'
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8008)
    args = parser.parse_args()
    uvicorn.run(create_export_demo(args.directory.resolve()), host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()
