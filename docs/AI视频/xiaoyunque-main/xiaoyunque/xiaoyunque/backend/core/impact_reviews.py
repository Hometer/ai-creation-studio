"""Conservative, atomic human review ledger bound to local project dependencies."""
import copy
import json
import os
import stat
import uuid
from datetime import datetime, timezone
from api.schemas.impact_reviews import ReviewHistory, ReviewWorkspace, ReviewRecord, ReviewSaved
from api.schemas.generation_plans import PlanStoreDocument
from core.content_revisions import ContentRevisions, digest, encoded, fail
from core.persistence import read_session, session_path
from core.asset_library import AssetLibraryError, unique_fields

MAX_REVIEWS = 8 * 1024 * 1024

def validate_reviews(raw):
    if not isinstance(raw,dict) or type(raw.get('schema_version')) is not int or raw['schema_version']!=1:
        raise ValueError('Unsupported review document')
    history=ReviewHistory.model_validate(raw)
    ids=set();requests=set()
    for n,r in enumerate(history.records,1):
        if r.number!=n or r.review_id in ids or r.request_id in requests or not r.note.strip(): raise ValueError('Invalid review history')
        datetime.fromisoformat(r.created_at.replace('Z','+00:00'))
        ids.add(r.review_id);requests.add(r.request_id)
    if len(encoded(raw))>MAX_REVIEWS: raise ValueError('Review history too large')
    return history


def locate(board, segment_id, shot_number):
    matches=[]
    def visit(obj,prefix):
        groups=[(obj.get('segments',[]),[*prefix,'segments'])]
        for n,ep in enumerate(obj.get('episodes',[]) if isinstance(obj.get('episodes'),list) else []):
            if isinstance(ep,dict): groups.append((ep.get('segments',[]),[*prefix,'episodes',n,'segments']))
        for segments,base in groups:
            for i,seg in enumerate(segments if isinstance(segments,list) else []):
                if not isinstance(seg,dict) or seg.get('segment_id')!=segment_id:continue
                if shot_number is None: matches.append((seg,[*base,i]))
                else:
                    for j,shot in enumerate(seg.get('shots',[]) if isinstance(seg.get('shots'),list) else []):
                        if isinstance(shot,dict) and type(shot.get('shot_number')) is int and shot['shot_number']==shot_number:
                            matches.append((shot,[*base,i,'shots',j]))
    visit(board,[])
    if isinstance(board.get('payload'),dict):visit(board['payload'],['payload'])
    if len(matches)!=1:return False,[]
    obj,path=matches[0]
    labels={'content':'镜头正文','dialogue':'台词','plot':'剧情','visual_prompt':'画面提示词','location':'地点','shot_type':'景别','duration':'时长'}
    fields=[{'path':[*path,key],'label':label,'value':str(obj[key])} for key,label in labels.items() if isinstance(obj.get(key),(str,int,float)) and not isinstance(obj.get(key),bool)]
    return True,fields

class ImpactReviews:
    def __init__(self,engine,library):
        self.engine,self.library=engine,library
        self.revisions=ContentRevisions(engine)

    def _read(self, project_id, library):
        state,history,current=self.revisions._read(project_id)
        try:
            disk=read_session(session_path(self.engine._session_dir,project_id),project_id)
            raw=getattr(state,'content_reviews',None)
            if raw!=disk.get('content_reviews') or encoded(state.artifacts)!=encoded(disk['artifacts']):raise ValueError('External update')
            ledger=validate_reviews(raw) if raw is not None else ReviewHistory()
            path=self.library.directory/'generation-plans.json'
            if path.is_symlink():raise ValueError('Unsafe plans')
            try:descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            except FileNotFoundError:plans=PlanStoreDocument()
            else:
                with os.fdopen(descriptor,'r',encoding='utf-8') as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError('Invalid plans file')
                    raw_plans=json.load(stream,object_pairs_hook=unique_fields)
                    if not isinstance(raw_plans,dict) or type(raw_plans.get('schema_version')) is not int or raw_plans['schema_version']!=1 or 'plans' not in raw_plans:raise ValueError('Unsupported plan document')
                    plans=PlanStoreDocument.model_validate(raw_plans)
            references={key:r.model_dump(mode='json') for key,r in library.references.items() if r.project_id==project_id}
            asset_ids={r['asset_id'] for r in references.values()}
            assets={key:a.model_dump(mode='json') for key,a in library.assets.items() if key in asset_ids}
            context=digest({'artifacts':state.artifacts,'status':state.status,'history':history.model_dump(),
                            'references':references,'assets':assets,'plans':{key:p.model_dump(mode='json') for key,p in plans.plans.items() if p.content.project_id==project_id}})
            return state,history,current,ledger,context
        except (OSError,ValueError,TypeError):fail(503,'REVIEW_STORE_UNAVAILABLE','核对记录或依据暂不可读取，请保留数据并检查服务')

    def _workspace(self,project_id,state,history,current,ledger,context):
        original=self.revisions._workspace(project_id,state,history,current)
        items=[];seen=set()
        for v in history.versions:
            for i in v.impacts:
                key=digest({'version':v.version_id,'segment':i.segment_id,'shot':i.shot_number,'reason':i.reason})
                if key in seen:continue
                seen.add(key)
                if len(seen)>1000:fail(422,'REVIEW_LIMIT','核对事项超过本轮支持上限，请保留数据并拆分项目')
                exists,fields=locate(current['storyboard'],i.segment_id,i.shot_number)
                latest=next((r for r in reversed(ledger.records) if r.item_id==key),None)
                status='pending' if latest is None else 'follow_up' if latest.decision=='follow_up' else 'retained' if latest.context_hash==context and exists and not original.untracked_stages else 'stale'
                items.append(dict(item_id=key,version_id=v.version_id,version_number=v.number,stage=v.stage,segment_id=i.segment_id,
                    shot_number=i.shot_number,reason=i.reason,current_fields=fields,target_exists=exists,status=status,latest_review_id=latest.review_id if latest else None))
        if len(items)>1000:fail(422,'REVIEW_LIMIT','核对事项超过本轮支持上限，请保留数据并拆分项目')
        if any(r.item_id not in {i['item_id'] for i in items} for r in ledger.records):fail(503,'REVIEW_STORE_UNAVAILABLE','核对记录与文字历史不一致，请保留数据')
        result=ReviewWorkspace(project_id=project_id,token=digest({'context':context,'ledger':ledger.model_dump()}),running=original.running,
            untracked_stages=original.untracked_stages,items=items,sources=[dict(version_id=v.version_id,version_number=v.number,stage=v.stage,changes=v.changes) for v in history.versions if v.impacts],records=[dict(**r.model_dump(),applicable=r.context_hash==context and not original.untracked_stages) for r in ledger.records])
        if len(encoded(result.model_dump()))>16*1024*1024:fail(422,'REVIEW_LIMIT','核对工作区超过本轮支持上限，请保留数据并拆分项目')
        return result

    def read(self,project_id):
        with self.engine._state_lock,self.library.locked() as library:
            return self._workspace(project_id,*self._read(project_id,library))

    def save(self,project_id,payload):
        with self.engine._state_lock,self.library.locked() as library:
            state,history,current,ledger,context=self._read(project_id,library)
            workspace=self._workspace(project_id,state,history,current,ledger,context)
            request_digest=digest(payload.model_dump())
            previous=next((r for r in ledger.records if r.request_id==payload.request_id),None)
            if previous:
                if previous.request_digest!=request_digest:fail(409,'REVIEW_REQUEST_REUSED','请求编号已用于其他核对，请重新读取')
                return ReviewSaved(review_id=previous.review_id,workspace=workspace)
            if workspace.running:fail(409,'PROJECT_RUNNING','项目正在执行，请结束后再核对')
            if payload.expected_token!=workspace.token:fail(409,'REVIEW_CHANGED','核对依据或记录已变化，请重新读取后核对')
            item=next((i for i in workspace.items if i.item_id==payload.item_id),None)
            if item is None:fail(404,'REVIEW_ITEM_NOT_FOUND','核对事项不存在')
            if payload.decision=='retain' and (not item.target_exists or workspace.untracked_stages):fail(409,'REVIEW_TARGET_UNAVAILABLE','镜头已移除、无法定位或文字尚未归档，请先处理后再核对')
            record=ReviewRecord(review_id=uuid.uuid4().hex,number=len(ledger.records)+1,item_id=payload.item_id,decision=payload.decision,
                note=payload.note,context_hash=context,request_id=payload.request_id,request_digest=request_digest,created_at=datetime.now(timezone.utc).isoformat())
            next_ledger=ledger.model_copy(deep=True);next_ledger.records.append(record)
            if len(next_ledger.records)>2000 or len(encoded(next_ledger.model_dump()))>MAX_REVIEWS:fail(422,'REVIEW_LIMIT','核对历史超过本轮支持上限，请保留数据并导出备份')
            had_ledger=hasattr(state,'content_reviews')
            old,old_time=copy.deepcopy(getattr(state,'content_reviews',None)),state.updated_at
            try:
                state.content_reviews=next_ledger.model_dump();state.updated_at=datetime.now()
                self.engine.save_session_to_disk(project_id)
            except Exception:
                state.updated_at=old_time
                if had_ledger:state.content_reviews=old
                else:del state.content_reviews
                fail(503,'REVIEW_SAVE_FAILED','核对保存未能确认，请重新读取核对，原记录保留')
            return ReviewSaved(review_id=record.review_id,workspace=self._workspace(project_id,state,history,current,next_ledger,context))
