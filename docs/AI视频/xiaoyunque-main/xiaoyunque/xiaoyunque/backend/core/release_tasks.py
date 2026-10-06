"""Snapshot persisted native tasks without calling providers or advancing jobs."""
from urllib.parse import quote
from api.schemas.release_tasks import ReleaseTask,ReleaseTasks
from core.compositions import Compositions

LABELS={'creation':'文字修改候选','script':'剧本候选','storyboard':'分镜候选','image':'参考图修订','video':'视频片段候选','composition':'成片合成'}
class ReleaseTasksReader:
    def __init__(self,engine,library,archive):self.engine=engine;self.films=Compositions(engine,library,archive)
    def read(self):
        rows=[]
        with self.films.transaction() as (ledger,store):
            def summary(job,kind,href,model=True,result=None,provider=None):
                project=job.plan.project_id;state=self.engine.get_state(project)
                name=state.meta.get('project_name') if state else None
                name=name.strip()[:100] if isinstance(name,str) and name.strip() else '未命名项目'
                rows.append(ReleaseTask(job_id=job.job_id,kind=kind,project_id=project,project_exists=state is not None,title=name+' · '+LABELS[kind]+' #'+str(job.number),number=job.number,created_at=job.created_at,status=job.status,simulation_only=job.simulation_only if model else any(c.simulation_only for c in job.plan.clips),model_call=model,estimate_fen=job.plan.estimate_fen if model else 0,reserved_fen=job.reserved_fen if model else 0,provider_task_id=provider,href=href,result_url=result,error_summary='任务需要在原入口核对；原结果与确认记录保留。' if job.error else None))
            for job in ledger.jobs:
                p=quote(job.plan.project_id,safe='');summary(job,'creation',f'/projects/{p}/co-creation?intent={job.plan.intent_id}&candidate={job.job_id}')
            for job in ledger.initial_jobs:
                p=quote(job.plan.project_id,safe='');summary(job,'script' if job.plan.kind=='script_generation' else 'storyboard',f'/projects/{p}/drafts?job={job.job_id}')
            for job in ledger.media_jobs:
                p=quote(job.plan.project_id,safe='');result=f'/api/assets/{job.asset_id}/versions/{job.version_id}/media' if job.status=='succeeded' and job.plan.kind=='video' else None
                summary(job,job.plan.kind,f'/projects/{p}/media?job={job.job_id}',result=result,provider=job.provider_task_id)
            for job in store.jobs:
                p=quote(job.plan.project_id,safe='');summary(job,'composition',f'/projects/{p}/compose?job={job.job_id}',model=False,result=f'/api/project/{p}/compositions/{job.job_id}/media' if job.status=='succeeded' else None)
        return ReleaseTasks(tasks=rows)
