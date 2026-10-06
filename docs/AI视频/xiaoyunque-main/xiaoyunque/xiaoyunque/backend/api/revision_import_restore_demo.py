"""Independent human backup restoration previews; no model calls."""
import argparse
from pathlib import Path
import uvicorn
from api.revision_import_preview_demo import create_preview_demo

def create_restore_demo(directory: Path):
    app = create_preview_demo(directory)
    app.title = '文字备份确认与恢复 · 人工样例，不调用模型'
    return app

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--port', type=int, default=8015)
    a = p.parse_args()
    uvicorn.run(create_restore_demo(a.directory.resolve()), host='127.0.0.1', port=a.port, access_log=False)
if __name__ == '__main__':
    main()
