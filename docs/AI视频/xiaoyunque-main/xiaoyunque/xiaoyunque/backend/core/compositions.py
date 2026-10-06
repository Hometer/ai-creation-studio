"""Durable free composition plans; every storyboard segment has an explicit clip."""
from contextlib import contextmanager
import time
from uuid import uuid4
from api.schemas.compositions import (ClipSelection,ClipOption,SegmentOption,CompositionPrepare,
    CompositionClip,CompositionPlan,CompositionJob,CompositionStore,CompositionView,CompositionWorkspace)
from api.schemas.assets import MediaInfo, MediaTaskSource, TaskSource
from core.asset_library import now
from core.creation_candidates import CreationCandidates, MAX_BYTES
from core.content_revisions import digest, fail, encoded
from core.generation_plans import GenerationPlans
from core.release_media import ReleaseMedia
from core.composition_renderer import CompositionRenderer
from core.task_provider import read_document
from core.persistence import atomic_write_session
from core.media_archive import sync_directory

DIMENSIONS={'16:9':(1280,720),'9:16':(720,1280),'1:1':(720,720),'4:3':(960,720),'3:4':(720,960)}

class Compositions:
    def __init__(self,engine,library,archive,renderer=None):
        self.engine,self.library,self.archive=engine,library,archive
        self.round=CreationCandidates(engine)
        self.path=self.round.directory/'compositions.json'
        self.media=ReleaseMedia(engine,library,archive,GenerationPlans(engine,library,archive))
        self.renderer=renderer if renderer is not None else CompositionRenderer()

    @contextmanager
    def transaction(self):
        # Engine -> shared first-release POSIX lock -> library, same order as media tasks.
        with self.round.transaction() as ledger:
            try:
                if self.path.is_symlink() or self.path.exists() and self.path.stat().st_size>MAX_BYTES:raise ValueError('Unsafe store')
                store=read_document(self.path,CompositionStore)
            except Exception:fail(503,'COMPOSITION_STORE_UNAVAILABLE','成片记录不可读取，请保留原文件')
            yield ledger,store

    def save(self,store):
        try:
            raw=store.model_dump(mode='json');CompositionStore.model_validate(raw)
            if len(encoded(raw))>MAX_BYTES:raise ValueError('Composition limit')
            atomic_write_session(self.path,raw);sync_directory(self.round.directory)
        except Exception:fail(503,'COMPOSITION_SAVE_UNCONFIRMED','成片保存结果尚未确认，请重新读取原任务')

    def basis(self,project,ledger):
        snapshot=self.engine.get_status_snapshot(project)
        if snapshot is None:fail(404,'PROJECT_NOT_FOUND','项目不存在')
        if project in self.engine._active_sessions:fail(409,'PROJECT_RUNNING','项目正在执行，请完成后再合成')
        board=snapshot['artifacts'].get('storyboard',{})
        episodes=board.get('episodes',[]) if isinstance(board,dict) else board
        if not isinstance(episodes,list):fail(409,'COMPOSITION_BOARD_INVALID','分镜结构无效，请先核对')
        segments=[]
        selections={s.target:s for s in ledger.media_selections if s.project_id==project and s.kind=='video'}
        for episode in episodes:
            if not isinstance(episode,dict) or not isinstance(episode.get('segments',[]),list):fail(409,'COMPOSITION_BOARD_INVALID','分镜结构无效，请先核对')
            for index,segment in enumerate(episode.get('segments',[]),1):
                if not isinstance(segment,dict):fail(409,'COMPOSITION_BOARD_INVALID','分镜片段无效')
                number=episode.get('episode_number',0)
                segment_id=segment.get('segment_id',f'seg_{number:02d}_{index:02d}' if type(number) is int else None)
                selected=selections.get(segment_id)
                try:segments.append(SegmentOption(segment_id=segment_id,label=str(segment.get('summary') or segment.get('description') or segment_id)[:200],selected=ClipSelection(segment_id=segment_id,asset_id=selected.asset_id,version_id=selected.version_id) if selected else None))
                except ValueError:fail(409,'COMPOSITION_BOARD_INVALID','分镜片段编号无效')
        if len(segments)>20 or len({s.segment_id for s in segments})!=len(segments):fail(409,'COMPOSITION_BOARD_INVALID','分镜片段重复或超过20段，请先核对')
        library=self.library.read(); options=[]
        for asset in library.assets.values():
            if asset.kind!='video':continue
            for version in asset.versions:
                source=version.source;job=next((j for j in ledger.media_jobs if isinstance(source,MediaTaskSource) and j.job_id==source.task_id),None)
                options.append(ClipOption(asset_id=asset.asset_id,version_id=version.version_id,name=version.name,number=version.number,media=version.media,source_type=source.type,source_project=source.project_id,simulation_only=isinstance(source,(TaskSource,MediaTaskSource)) and source.simulation_only,stale=isinstance(source,MediaTaskSource) and (job is None or job.plan.project_id!=project or not self.media.current(job))))
        refs=[r.model_dump() for r in library.references.values() if r.project_id==project]
        meta=snapshot.get('meta',{});ratio=meta.get('video_ratio') or '16:9';target=meta.get('target_duration')
        token=digest({'snapshot':snapshot['artifacts'],'goals':{k:meta.get(k) for k in ['project_name','target_duration','style','video_ratio']},'references':refs,'options':[o.model_dump() for o in options],'segments':[s.model_dump() for s in segments]})
        return token,segments,options,ratio,target

    @staticmethod
    def find(store,project,job_id):
        job=next((j for j in store.jobs if j.job_id==job_id and j.plan.project_id==project),None)
        if job is None:fail(404,'COMPOSITION_NOT_FOUND','成片任务不存在或不属于这个项目')
        return job

    def view(self,ledger,job):
        try:current=self.basis(job.plan.project_id,ledger)[0]==job.plan.expected_workspace
        except Exception:current=False
        return CompositionView(**job.model_dump(exclude={'prepare','confirmation','lease_until'}),current=current)

    def workspace(self,project):
        with self.transaction() as (ledger,store):
            token,segments,options,ratio,target=self.basis(project,ledger)
            return CompositionWorkspace(project_id=project,expected_workspace=token,segments=segments,options=options,
                jobs=[self.view(ledger,j) for j in store.jobs if j.plan.project_id==project],video_ratio=ratio,target_duration=target,tools_available=self.renderer.available())

    def compile(self,project,payload,ledger):
        token,segments,options,ratio,target=self.basis(project,ledger)
        if token!=payload.expected_workspace:fail(409,'COMPOSITION_BASIS_CHANGED','分镜、选用视频或目标已改变，请重新读取')
        if not segments or {s.segment_id for s in segments}!={c.segment_id for c in payload.clips}:fail(409,'COMPOSITION_CLIPS_INCOMPLETE','请为每个分镜片段明确选用一个视频，不能缺段或额外添加片段')
        if ratio not in DIMENSIONS:fail(422,'COMPOSITION_RATIO_UNSUPPORTED','当前项目比例不支持本机合成，请先核对项目比例')
        clips=[]
        for selection in payload.clips:
            option=next((o for o in options if o.asset_id==selection.asset_id and o.version_id==selection.version_id),None)
            if option is None:fail(409,'COMPOSITION_CLIP_MISSING','所选归档视频版本不存在')
            self.archive.verify_file(option.media)
            clips.append(CompositionClip(**selection.model_dump(),name=option.name,version_number=option.number,media=option.media,simulation_only=option.simulation_only,stale=option.stale))
        if set(payload.keep_stale)!={c.segment_id for c in clips if c.stale}:fail(409,'COMPOSITION_STALE_REVIEW_REQUIRED','上游已变化的片段需逐段确认保留旧版本')
        duration=sum(c.media.duration for c in clips)
        if duration>600 or sum(c.media.byte_size for c in clips)>500*1024*1024:fail(422,'COMPOSITION_INPUT_LIMIT','合成输入最多600秒、500MB，请明确缩小分镜后再核对')
        return CompositionPlan(project_id=project,expected_workspace=token,clips=clips,keep_stale=payload.keep_stale,video_ratio=ratio,width=DIMENSIONS[ratio][0],height=DIMENSIONS[ratio][1],target_duration=target,total_duration=duration)

    def prepare(self,project,payload):
        with self.transaction() as (ledger,store):
            old=next((j for j in store.jobs if j.prepare.request_id==payload.request_id),None)
            if old:
                if old.plan.project_id!=project or old.prepare!=payload:fail(409,'COMPOSITION_REQUEST_REUSED','请求编号已用于另一份计划')
                return self.view(ledger,old)
            if len(store.jobs)>=100:fail(422,'COMPOSITION_HISTORY_LIMIT','合成记录达到本机上限，旧记录继续保留')
            if any(j.confirmation and j.confirmation.request_id==payload.request_id for j in store.jobs):fail(409,'COMPOSITION_REQUEST_REUSED','请求编号已用于确认')
            plan=self.compile(project,payload,ledger)
            job=CompositionJob(job_id=uuid4().hex,number=len(store.jobs)+1,prepare=payload,plan=plan,plan_hash=digest(plan.model_dump()),created_at=now())
            store.jobs.append(job);self.save(store);return self.view(ledger,job)

    def get(self,project,job_id):
        with self.transaction() as (ledger,store):return self.view(ledger,self.find(store,project,job_id))

    def confirm(self,project,job_id,payload):
        with self.transaction() as (ledger,store):
            job=self.find(store,project,job_id)
            if job.confirmation:
                if job.confirmation!=payload:fail(409,'COMPOSITION_ALREADY_CONFIRMED','任务已有确认，请读取原任务')
                return self.view(ledger,job)
            if any(j.prepare.request_id==payload.request_id or j.confirmation and j.confirmation.request_id==payload.request_id for j in store.jobs):fail(409,'COMPOSITION_REQUEST_REUSED','请求编号已被使用')
            if job.status!='draft' or payload.plan_hash!=job.plan_hash:fail(409,'COMPOSITION_PLAN_CHANGED','计划不可确认，请核对原任务')
            if not self.renderer.available():fail(503,'COMPOSITION_TOOLS_MISSING','本机合成工具不可用，未执行')
            if digest(self.compile(project,job.prepare,ledger).model_dump())!=job.plan_hash:fail(409,'COMPOSITION_PLAN_CHANGED','输入依据已改变，请重新准备')
            job.confirmation=payload;job.confirmed_at=now();job.status='queued';self.save(store);return self.view(ledger,job)

    def stop(self,project,job_id):
        with self.transaction() as (ledger,store):
            job=self.find(store,project,job_id)
            if job.status not in {'draft','queued','cancelled'}:fail(409,'COMPOSITION_ALREADY_STARTED','任务已开始或结束，请保留原结果并核对状态')
            job.status='cancelled';self.save(store);return self.view(ledger,job)

    def advance(self,project,job_id,stop_event=None):
        with self.transaction() as (ledger,store):
            job=self.find(store,project,job_id)
            if job.status=='rendering':
                if job.output:
                    try:self.archive.verify_file(job.output)
                    except Exception:pass
                    else:job.status='succeeded';job.lease_until=None;self.save(store)
                if job.status=='rendering' and (job.lease_until or 0)<=int(time.time()):
                    job.status='manual_review';job.error='上次本机渲染结果尚未确认，未重复合成；可核对历史后明确准备新的免费计划。';self.save(store)
                return self.view(ledger,job)
            if job.status!='queued':return self.view(ledger,job)
            # Claim only once before launching any process, including failures at setup.
            job.status='rendering';job.attempts=1;job.lease_until=int(time.time())+360;self.save(store)
            owned=job.model_copy(deep=True)
        output=None;error=None;state='failed'
        try:
            with self.transaction() as (ledger,store):
                if digest(self.compile(project,owned.prepare,ledger).model_dump())!=owned.plan_hash:raise ValueError('Basis changed')
            with self.renderer.render(owned,self.archive,stop_event) as result:
                with self.archive.prepare(result,'video') as (temporary,details):
                    if details['width']!=owned.plan.width or details['height']!=owned.plan.height or abs(details['duration']-owned.plan.total_duration)>.2:raise ValueError('Invalid film output')
                    output=MediaInfo(file_id=owned.job_id,**details)
                    # Persist checksum before exclusive publish; restart can verify that exact file.
                    with self.transaction() as (ledger,store):
                        current=self.find(store,project,job_id)
                        if current.status!='rendering':raise ValueError('Claim changed')
                        current.output=output;self.save(store)
                    self.archive.publish(temporary,output)
                    state='succeeded'
        except Exception:
            error='本机合成未完成，原片段与旧成片已保留；请读取任务核对后明确准备新的免费计划。'
            if stop_event is not None and stop_event.is_set():state='manual_review'
        with self.transaction() as (ledger,store):
            job=self.find(store,project,job_id)
            if job.status=='rendering':
                job.status=state;job.error=error;job.lease_until=None
                if state!='succeeded':job.output=None
                self.save(store)
            return self.view(ledger,job)

    def tick(self,stop_event=None):
        with self.transaction() as (_,store):
            jobs=[(j.plan.project_id,j.job_id) for j in store.jobs if j.status in {'queued','rendering'}][:20]
        for project,job_id in jobs:
            if stop_event is not None and stop_event.is_set():break
            self.advance(project,job_id,stop_event)

    def output_path(self,project,job_id):
        with self.transaction() as (_,store):
            job=self.find(store,project,job_id)
            if job.status!='succeeded' or not job.output:fail(409,'COMPOSITION_NOT_READY','成片尚未完成，请核对原任务')
            return self.archive.verify_file(job.output)
