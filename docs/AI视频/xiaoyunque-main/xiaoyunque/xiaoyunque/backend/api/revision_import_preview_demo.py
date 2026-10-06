"""Independent human backup restoration previews; no model or import writes."""
import argparse
from pathlib import Path
import uvicorn
from api.revision_import_precheck_demo import create_precheck_demo

def create_preview_demo(directory: Path):
    app = create_precheck_demo(directory)
    app.title = '文字备份恢复预览 · 人工样例，不调用模型'
    return app

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--port', type=int, default=8014)
    a = p.parse_args()
    uvicorn.run(create_preview_demo(a.directory.resolve()), host='127.0.0.1', port=a.port, access_log=False)
if __name__ == '__main__':
    main()
