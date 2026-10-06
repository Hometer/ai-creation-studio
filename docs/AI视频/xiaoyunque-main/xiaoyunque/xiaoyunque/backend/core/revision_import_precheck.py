"""Strict untrusted portable backup validation; no file, project or history writes."""
import hashlib
import json
import re
from datetime import datetime
from pydantic import ValidationError
from api.schemas.revision_exports import ExportDocument
from api.schemas.revision_import_precheck import PrecheckReport, StageSummary, VersionSummary
from core.content_revisions import ContentRevisions, STAGES, MAX_CONTENT, MAX_HISTORY, encoded, digest, fields, fail
from core.revision_exports import MAX_EXPORT_BYTES

MAX_DEPTH = 64
MAX_NODES = 500000

def check_filename(name):
    if not isinstance(name,str) or not 1<=len(name)<=128 or name!=name.strip() or not name.lower().endswith('.json') or any(c in name for c in '/\\') or any(ord(c)<32 or ord(c)==127 for c in name):
        fail(415,'BACKUP_FILENAME','请选择文件名安全的 JSON 文字备份，不支持路径或其他格式')

def pairs(items):
    result={}
    for key,value in items:
        if key in result:raise ValueError('Duplicate JSON key')
        result[key]=value
    return result

def refuse_constant(_):raise ValueError('Non-finite JSON value')

def parse_backup(body):
    if not body or len(body)>MAX_EXPORT_BYTES:
        fail(413,'BACKUP_SIZE','文字备份必须非空且不超过 64 MiB')
    try:
        text=body.decode('utf-8-sig')
        if not text.lstrip().startswith('{'):raise ValueError('Not JSON object')
        # Limit nesting before the parser builds objects. Ignore braces inside JSON strings.
        depth=0;quoted=False;escaped=False
        for c in text:
            if quoted:
                if escaped:escaped=False
                elif c=='\\':escaped=True
                elif c=='"':quoted=False
            elif c=='"':quoted=True
            elif c in '[{':
                depth+=1
                if depth>MAX_DEPTH:raise ValueError('Nesting limit')
            elif c in ']}':depth-=1
        raw=json.loads(text,object_pairs_hook=pairs,parse_constant=refuse_constant)
        pending=[raw];nodes=0
        while pending:
            value=pending.pop();nodes+=1
            if nodes>MAX_NODES:raise ValueError('Node limit')
            if isinstance(value,dict):pending.extend(value.values())
            elif isinstance(value,list):pending.extend(value)
        if not isinstance(raw,dict) or set(raw)!={'format','schema_version','project_id','token','current','versions','untracked_stages'} or type(raw.get('schema_version')) is not int:raise ValueError('Schema type')
        doc=ExportDocument.model_validate(raw)
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',doc.project_id) or set(doc.current)!=set(STAGES):raise ValueError('Invalid scope')
        previous={};latest={}
        if len(encoded([v.model_dump() for v in doc.versions]))>MAX_HISTORY:raise ValueError('History size')
        def validate_content(stage,content,checksum):
            if len(encoded(content))>MAX_CONTENT or digest(content)!=checksum:raise ValueError('Invalid snapshot')
            fields(stage,content)
        for index,v in enumerate(doc.versions):
            if v.number!=index+1 or v.version_id in previous:raise ValueError('Version sequence')
            datetime.fromisoformat(v.created_at.replace('Z','+00:00'))
            if v.source=='restore':
                if previous.get(v.restored_from)!=v.stage:raise ValueError('Restore relation')
            elif v.restored_from is not None:raise ValueError('Unexpected restore relation')
            validate_content(v.stage,v.content,v.digest)
            previous[v.version_id]=v.stage;latest[v.stage]=v
        untracked=[]
        for stage in STAGES:
            s=doc.current[stage];v=latest.get(stage)
            validate_content(stage,s.content,s.digest)
            expected=v.version_id if v and v.digest==s.digest else None
            if s.version_id!=expected:raise ValueError('Current relation')
            if v and v.digest!=s.digest:untracked.append(stage)
        if doc.untracked_stages!=untracked:raise ValueError('Untracked relation')
        return doc
    except (UnicodeError,ValueError,TypeError,AttributeError,RecursionError,ValidationError,OverflowError):
        fail(422,'BACKUP_INVALID','备份格式、摘要或版本关系无法校验，请保留原文件并重新导出 JSON 备份')

class RevisionImportPrecheck:
    def __init__(self,engine):self.engine=engine
    def inspect(self,project_id,expected_token,filename,body):
        check_filename(filename)
        doc=parse_backup(body)
        with self.engine._state_lock:
            _,history,current=ContentRevisions(self.engine)._read(project_id)
            token=digest({'current':current,'history':history.model_dump()})
            if token!=expected_token:fail(409,'REVISION_CHANGED','当前文字或历史已变化，请重新读取版本后再检查备份')
            stages={stage:StageSummary(digest=doc.current[stage].digest,
                field_count=len(fields(stage,doc.current[stage].content)),
                version_count=sum(v.stage==stage for v in doc.versions),
                matches_current=doc.current[stage].digest==digest(current[stage]),
                untracked=stage in doc.untracked_stages,version_id=doc.current[stage].version_id) for stage in STAGES}
        return PrecheckReport(project_id=project_id,token=token,source_project_id=doc.project_id,
            same_project=doc.project_id==project_id,file_bytes=len(body),file_sha256=hashlib.sha256(body).hexdigest(),
            stages=stages,versions=[VersionSummary(**{k:getattr(v,k) for k in ('version_id','number','stage','source','created_at','restored_from')},
                field_count=len(fields(v.stage,v.content))) for v in doc.versions])
