"""One shared round ledger; approval -> one submit -> same-ID recovery -> candidates."""
import base64
import hashlib
import time
from datetime import datetime,timezone
from uuid import uuid4
from api.schemas.release_media import MediaPlan,MediaJob,MediaView,MediaWorkspace,ImageOption,MediaSelection
from api.schemas.assets import MediaTaskSource,VersionInput,ReferenceUpdate
from core.creation_candidates import CreationCandidates,PRICE_EXPIRES
from core.release_media_provider import ReleaseMediaProvider
from core.content_revisions import digest,fail
from core.generation_plans import find_segment
from core.model_preflight import inspect_target

PRICE='aliyun-beijing-media-20261006'

class ReleaseMedia:
    def __init__(self,engine,library,archive,plans,provider=None):
        self.engine,self.library,self.archive,self.plans=engine,library,archive,plans
        self.provider=provider if provider is not None else ReleaseMediaProvider()
        self.round=CreationCandidates(engine)

    def snapshot(self,project):
        value=self.engine.get_status_snapshot(project)
        if value is None:fail(404,'PROJECT_NOT_FOUND','项目不存在')
        if project in self.engine._active_sessions:fail(409,'PROJECT_RUNNING','项目正在执行，请完成后再核对')
        return value

    def workspace_token(self,project):
        snapshot=self.snapshot(project);data=self.library.read()
        refs=[r.model_dump() for r in data.references.values() if r.project_id==project]
        return digest({'project':project,'artifacts':snapshot['artifacts'],'references':refs})

    def compile(self,project,payload):
        snapshot=self.snapshot(project)
        prompt=payload.prompt+('\n必须保留：'+payload.preserve if payload.preserve else '')
        if payload.kind=='image':
            library=self.library.read();ref=library.references.get(payload.source_id)
            if ref is None or ref.project_id!=project or ref.segment_id!=payload.segment_id:fail(409,'MEDIA_REFERENCE_CHANGED','请选择本项目当前明确的图片引用')
            asset=library.assets[ref.asset_id];version=next(v for v in asset.versions if v.version_id==ref.version_id)
            if asset.kind!='image' or len(prompt)>800:fail(422,'IMAGE_INPUT_UNSUPPORTED','图片编辑需图片引用，修改与保留说明合计不超过800字')
            m=version.media
            if not 512<=m.width<=4096 or not 512<=m.height<=4096:fail(422,'IMAGE_DIMENSIONS_UNSUPPORTED','图片编辑输入宽高须为512—4096像素')
            path=self.archive.verify_file(m);raw=path.read_bytes()
            if len(raw)!=m.byte_size or hashlib.sha256(raw).hexdigest()!=m.sha256:fail(409,'MEDIA_BYTES_CHANGED','图片字节已变化，请重新核对')
            body={'model':'wanx2.1-imageedit','input':{'function':'description_edit','prompt':prompt,'base_image_url':'data:'+m.mime_type+';base64,'+base64.b64encode(raw).decode()},'parameters':{'n':1,'watermark':False}}
            from api.schemas.model_preflight import ImageCheck
            inputs=[ImageCheck(reference_id=ref.reference_id,asset_id=asset.asset_id,version_id=version.version_id,role='reference_image',width=m.width,height=m.height,byte_size=m.byte_size,sha256=m.sha256)]
            segment=find_segment(snapshot,ref.segment_id) if ref.segment_id else None
            if ref.segment_id and segment is None:fail(409,'MEDIA_TARGET_MISSING','原图片引用的片段不存在')
            token=digest({'reference':ref.model_dump(),'version':version.model_dump(),'segment':segment})
            return body,inputs,token,None,None,14
        plan=self.plans.get(payload.source_id)
        if plan.content.project_id!=project or not plan.current or plan.confirmation is None:fail(409,'MEDIA_PLAN_CHANGED','视频需本项目当前有效且已确认的输入计划')
        target=next((t for t in plan.content.targets if t.segment_id==payload.segment_id),None)
        if target is None:fail(404,'MEDIA_TARGET_MISSING','所选片段不在计划中')
        check,request=inspect_target(plan.content,target,self.archive,True)
        if not check.input_protocol_ready or request is None:fail(422,'MEDIA_PROTOCOL_UNSUPPORTED','计划不符合已核对万相首帧／首尾帧协议，请到生成计划核对')
        body=request.model_dump(mode='json');body['input']['prompt']=target.prompt+'\n本次修改：'+prompt
        if len(body['input']['prompt'])>5000:fail(422,'MEDIA_PROMPT_LIMIT','完整分镜、修改和保留说明超过5000字，本次不发送')
        amount=target.duration*(60 if plan.content.options.resolution=='720P' else 100)
        if amount>500:fail(409,'MEDIA_SINGLE_LIMIT','此规格估算超过单次5元，请明确新建更短或更低规格的计划后再核对')
        token=digest({'content_hash':plan.content_hash,'target':target.model_dump()})
        return body,check.images,token,target.duration,plan.content.options.resolution,amount

    def current(self,job):
        try:
            snapshot=self.snapshot(job.plan.project_id);p=job.prepare
            prompt=p.prompt+('\n必须保留：'+p.preserve if p.preserve else '')
            if p.kind=='image':
                data=self.library.read();ref=data.references[p.source_id];asset=data.assets[ref.asset_id]
                version=next(v for v in asset.versions if v.version_id==ref.version_id)
                segment=find_segment(snapshot,ref.segment_id) if ref.segment_id else None
                token=digest({'reference':ref.model_dump(),'version':version.model_dump(),'segment':segment})
                return ref.project_id==job.plan.project_id and ref.segment_id==p.segment_id and token==job.plan.expected_token and prompt==job.plan.execution_prompt
            plan=self.plans.get(p.source_id);target=next(t for t in plan.content.targets if t.segment_id==p.segment_id)
            token=digest({'content_hash':plan.content_hash,'target':target.model_dump()})
            return plan.content.project_id==job.plan.project_id and plan.current and plan.confirmation is not None and token==job.plan.expected_token and target.prompt+'\n本次修改：'+prompt==job.plan.execution_prompt
        except Exception:return False

    @staticmethod
    def target(job):return job.plan.segment_id if job.plan.kind=='video' else job.plan.source_id

    def selection_token(self,ledger,job):
        old=[s.model_dump() for s in ledger.media_selections if s.project_id==job.plan.project_id and s.kind==job.plan.kind and s.target==self.target(job)]
        return digest(old)

    def view(self,ledger,job):
        selection=next((s for s in reversed(ledger.media_selections) if s.project_id==job.plan.project_id and s.kind==job.plan.kind and s.target==self.target(job)),None)
        return MediaView(**job.model_dump(exclude={'prepare','confirm_request_id','confirm_digest','submitted_at','failures','next_at','lease_until','result_url'}),current=self.current(job),selected=(self.image_selected(job) if job.plan.kind=='image' else selection is not None and selection.job_id==job.job_id),selection_token=self.selection_token(ledger,job))

    def image_selected(self,job):
        try:
            ref=self.library.read().references[job.plan.source_id]
            return job.version_id is not None and ref.version_id==job.version_id and ref.asset_id==job.asset_id
        except Exception:return False

    def image_selection_recoverable(self,job):
        try:
            data=self.library.read();ref=data.references[job.plan.source_id]
            return self.image_selected(job) and ref.project_id==job.plan.project_id and ref.segment_id==job.plan.segment_id and ref.revision==job.plan.reference_revision+1 and ref.history[-2].version_id==job.plan.input_refs[0].version_id
        except Exception:return False

    @staticmethod
    def find(ledger,project,job_id):
        job=next((j for j in ledger.media_jobs if j.job_id==job_id and j.plan.project_id==project),None)
        if job is None:fail(404,'MEDIA_TASK_NOT_FOUND','媒体任务不存在或不属于这个项目')
        return job

    def workspace(self,project):
        with self.round.transaction() as ledger:
            token=self.workspace_token(project);data=self.library.read();images=[]
            for ref in data.references.values():
                if ref.project_id!=project:continue
                asset=data.assets[ref.asset_id]
                if asset.kind=='image':images.append(ImageOption(reference=ref,asset=asset,version=next(v for v in asset.versions if v.version_id==ref.version_id)))
            connection=self.provider.connection();occupied=ledger.occupied()
            return MediaWorkspace(project_id=project,expected_workspace=token,credential_present=connection.credential_present,endpoint_allowed=connection.endpoint_allowed,price_current=time.time()<PRICE_EXPIRES,occupied_fen=occupied,available_fen=2000-occupied,images=images,jobs=[self.view(ledger,j) for j in ledger.media_jobs if j.plan.project_id==project],selections=[s for s in ledger.media_selections if s.project_id==project])

    def prepare(self,project,payload):
        with self.round.transaction() as ledger:
            old=next((j for j in ledger.media_jobs if j.prepare.request_id==payload.request_id),None)
            if old:
                if old.plan.project_id!=project or old.prepare!=payload:fail(409,'MEDIA_REQUEST_REUSED','请求编号已经用于其他计划')
                return self.view(ledger,old)
            if payload.request_id in ledger.used_requests():fail(409,'MEDIA_REQUEST_REUSED','请求编号已经用于其他操作')
            if self.workspace_token(project)!=payload.expected_workspace:fail(409,'MEDIA_WORKSPACE_CHANGED','工作区依据已变化，请重新读取')
            if time.time()>=PRICE_EXPIRES:fail(409,'MEDIA_PRICE_EXPIRED','价格依据已过期，请更新后再生成')
            body,images,token,duration,resolution,amount=self.compile(project,payload)
            plan=MediaPlan(project_id=project,kind=payload.kind,source_id=payload.source_id,segment_id=payload.segment_id,expected_token=token,request_digest=digest(body),reference_revision=self.library.read().references[payload.source_id].revision if payload.kind=='image' else None,model=body['model'],execution_prompt=body['input']['prompt'],preserve=payload.preserve,duration=duration,resolution=resolution,input_refs=images,estimate_fen=amount,price_version=PRICE,expires_at=min(int(time.time())+1200,PRICE_EXPIRES))
            job=MediaJob(job_id=uuid4().hex,number=len(ledger.media_jobs)+1,prepare=payload,plan=plan,plan_hash=digest(plan.model_dump()),created_at=datetime.now(timezone.utc).isoformat(),simulation_only=self.provider.simulation_only)
            ledger.media_jobs.append(job);self.round.save(ledger);return self.view(ledger,job)

    def confirm(self,project,job_id,payload):
        with self.round.transaction() as ledger:
            job=self.find(ledger,project,job_id);signature=digest(payload.model_dump())
            if job.confirm_request_id:
                if job.confirm_request_id!=payload.request_id or job.confirm_digest!=signature:fail(409,'MEDIA_ALREADY_CONFIRMED','已有确认，请读取原任务')
                return self.view(ledger,job)
            if payload.request_id in ledger.used_requests():fail(409,'MEDIA_REQUEST_REUSED','请求编号已经用于其他操作')
            if job.status!='awaiting_confirmation' or payload.plan_hash!=job.plan_hash or not self.current(job):fail(409,'MEDIA_BASIS_CHANGED','计划或输入依据变化，请重新核对')
            if time.time()>=min(job.plan.expires_at,PRICE_EXPIRES):fail(409,'MEDIA_PRICE_EXPIRED','计划或价格已过期')
            try:self.provider.initialize()
            except Exception:fail(409,'MEDIA_PROVIDER_UNAVAILABLE','后端Key或境内接口尚不可用，请配置后再核对')
            if ledger.occupied()+job.plan.estimate_fen>2000:fail(409,'ROUND_BUDGET_EXCEEDED','本轮累计估算超过20元，未提交')
            job.confirm_request_id,job.confirm_digest=payload.request_id,signature
            job.reserved_fen,job.status=job.plan.estimate_fen,'queued';self.round.save(ledger);return self.view(ledger,job)

    def get(self,project,job_id):
        with self.round.transaction() as ledger:return self.view(ledger,self.find(ledger,project,job_id))

    def advance(self,project,job_id):
        with self.round.transaction() as ledger:
            job=self.find(ledger,project,job_id);now=int(time.time())
            if job.status in {'submitting','querying','downloading'}:
                if job.lease_until is None or now<=job.lease_until:return self.view(ledger,job)
                job.status='manual_review' if job.status=='submitting' else ('pending' if job.status=='querying' else 'ready_download')
                job.error='原提交结果未知，不会重新提交' if job.status=='manual_review' else '读取中断，将恢复同一任务'
                self.round.save(ledger)
            if job.status not in {'queued','pending','ready_download'} or job.next_at>now:return self.view(ledger,job)
            action=job.status
            if action=='queued':
                if not self.current(job) or now>=min(job.plan.expires_at,PRICE_EXPIRES):
                    job.status,job.error='failed','依据或价格已变化，未发送';self.round.save(ledger);return self.view(ledger,job)
                try:body,*_=self.compile(project,job.prepare)
                except Exception:
                    job.status,job.error='failed','输入素材校验未通过，未发送；请核对原素材后重新准备'
                    self.round.save(ledger);return self.view(ledger,job)
            elif now>=job.submitted_at+86400 or (action=='pending' and job.query_attempts>=30):
                job.status,job.error='manual_review','原任务查询窗口或次数达到上限，保留记录，不重发生成';self.round.save(ledger);return self.view(ledger,job)
            try:transport=self.provider.initialize()
            except Exception:
                job.status,job.error='failed' if action=='queued' else 'manual_review','配置不可用，未开始新的收费请求';self.round.save(ledger);return self.view(ledger,job)
            if action=='queued':job.status,job.submit_attempts,job.submitted_at='submitting',1,now
            elif action=='pending':job.status='querying';job.query_attempts+=1
            else:job.status='downloading'
            job.lease_until=now+45;self.round.save(ledger);claimed=job.model_copy(deep=True)
        try:
            if action=='queued':result=self.provider.submit(claimed,body,transport)
            elif action=='pending':result=self.provider.query(claimed,transport)
            else:
                path=self.provider.download(claimed,self.round.directory/'media-results'/claimed.job_id,transport)
                source=MediaTaskSource(project_id=project,stage='video_generation' if claimed.plan.kind=='video' else 'reference_generation',item_type='clips' if claimed.plan.kind=='video' else 'scenes',item_id=self.target(claimed),task_id=claimed.job_id,plan_hash=claimed.plan_hash,provider_task_id=claimed.provider_task_id,simulation_only=claimed.simulation_only)
                with self.archive.prepare(path,claimed.plan.kind) as (temporary,details):
                    if claimed.plan.kind=='video' and abs(details['duration']-claimed.plan.duration)>0.5:raise ValueError('Unexpected downloaded duration')
                    asset=self.library.import_media(source,claimed.plan.kind,VersionInput(name=('视频' if claimed.plan.kind=='video' else '图片')+'候选 '+str(claimed.number),definition='按明确计划生成的候选；'+('人工接口样例，不代表模型结果。' if claimed.simulation_only else '保留要素仍需人工核对。')),self.archive,temporary,details,asset_id=claimed.plan.input_refs[0].asset_id if claimed.plan.kind=='image' else None)
                version=next(v for v in asset.versions if isinstance(v.source,MediaTaskSource) and v.source.task_id==claimed.job_id);result=(asset.asset_id,version.version_id)
        except Exception:
            with self.round.transaction() as ledger:
                job=self.find(ledger,project,job_id)
                if action=='queued':job.status,job.error='manual_review','提交回复未确认，请核对原平台；不会重发收费请求'
                else:
                    job.failures+=1;job.status='manual_review' if job.failures>=3 else ('pending' if action=='pending' else 'ready_download');job.next_at=int(time.time())+5;job.error='原任务查询／归档尚未确认，保留ID及占用；不会重新生成'
                self.round.save(ledger);return self.view(ledger,job)
        with self.round.transaction() as ledger:
            job=self.find(ledger,project,job_id);job.failures=0;job.error=None;job.lease_until=None
            if action=='queued':job.provider_task_id=result;job.status='pending';job.next_at=int(time.time())+5
            elif action=='pending':
                state,url,cost=result
                if state=='SUCCEEDED':job.status,job.result_url,job.usage_estimate_fen='ready_download',url,cost;job.next_at=0
                elif state in {'FAILED','CANCELED'}:job.status,job.error='failed','平台明确失败或取消，旧结果保留'
                elif state=='UNKNOWN':job.status,job.error='manual_review','平台原任务状态未知，不重新提交'
                else:job.status='pending';job.next_at=int(time.time())+min(60,5*(1+job.query_attempts//3))
            else:job.asset_id,job.version_id=result;job.status='succeeded'
            self.round.save(ledger);return self.view(ledger,job)

    def action(self,project,job_id,resume):
        with self.round.transaction() as ledger:
            job=self.find(ledger,project,job_id)
            if resume:
                if not job.provider_task_id or job.status not in {'manual_review','cancelled'} or job.query_attempts>=30 or time.time()>=job.submitted_at+86400:fail(409,'MEDIA_RESUME_UNAVAILABLE','只有窗口内已有平台ID的原任务可以恢复查询，不能重发生成')
                job.status='ready_download' if job.result_url else 'pending';job.failures=0;job.next_at=0;job.error=None
            else:
                if job.status in {'succeeded','failed','submitting','querying','downloading'}:fail(409,'MEDIA_STOP_UNAVAILABLE','正在读取或已结束，请先刷新核对')
                job.status,job.error='cancelled','已停止本地后续步骤；外部任务可能仍在执行，占用与记录保留'
            self.round.save(ledger);return self.view(ledger,job)

    def select(self,project,job_id,payload):
        with self.round.transaction() as ledger:
            job=self.find(ledger,project,job_id)
            old=next((s for s in ledger.media_selections if s.request_id==payload.request_id),None)
            if old:
                if old.job_id!=job_id or old.project_id!=project or old.previous_token!=payload.expected_selection:fail(409,'MEDIA_REQUEST_REUSED','选用请求编号已经用于其他操作')
                return self.view(ledger,job)
            if payload.request_id in ledger.used_requests():fail(409,'MEDIA_REQUEST_REUSED','请求编号已经用于其他操作')
            if job.status!='succeeded' or not (self.current(job) or job.plan.kind=='image' and self.image_selection_recoverable(job)) or self.selection_token(ledger,job)!=payload.expected_selection:fail(409,'MEDIA_SELECTION_CHANGED','候选或当前选择依据已变化，请重新核对')
            data=self.library.read();asset=data.assets.get(job.asset_id);version=next((v for v in asset.versions if v.version_id==job.version_id),None) if asset else None
            if version is None:fail(409,'MEDIA_RESULT_MISSING','候选结果不存在')
            self.archive.verify_file(version.media)
            if job.plan.kind=='image' and not self.image_selected(job):
                self.library.update_reference(job.plan.source_id,ReferenceUpdate(version_id=job.version_id,expected_revision=job.plan.reference_revision))
            ledger.media_selections.append(MediaSelection(request_id=payload.request_id,job_id=job_id,project_id=project,kind=job.plan.kind,target=self.target(job),asset_id=job.asset_id,version_id=job.version_id,selected_at=datetime.now(timezone.utc).isoformat(),previous_token=payload.expected_selection))
            self.round.save(ledger);return self.view(ledger,job)

    def tick(self):
        with self.round.transaction() as ledger:
            ids=[(j.plan.project_id,j.job_id) for j in ledger.media_jobs if j.status in {'queued','submitting','pending','querying','ready_download','downloading'} and j.simulation_only==self.provider.simulation_only]
        for project,job in ids[:20]:self.advance(project,job)
