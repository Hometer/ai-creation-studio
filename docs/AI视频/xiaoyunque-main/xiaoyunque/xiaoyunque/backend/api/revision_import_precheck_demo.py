"""Independent human text backups, no model or restoration writes."""
import argparse
from pathlib import Path
import uvicorn
from api.export_demo import create_export_demo

def create_precheck_demo(directory:Path):
    app=create_export_demo(directory)
    app.title='文字备份只读预检 · 人工样例，不调用模型'
    return app

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--port',type=int,default=8013)
    a=p.parse_args();uvicorn.run(create_precheck_demo(a.directory.resolve()),host='127.0.0.1',port=a.port,access_log=False)
if __name__=='__main__':main()
