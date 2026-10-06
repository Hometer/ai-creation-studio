"""Goal-only writes through the existing atomic session persistence."""
from datetime import datetime,timezone
from pydantic import TypeAdapter
from api.schemas.project_goals import GoalRecord,GoalView
from core.content_revisions import digest,fail
class ProjectGoals:
    def __init__(self,engine):self.engine=engine
    def state(self,project):
        state=self.engine.get_state(project)
        if state is None:fail(404,'PROJECT_NOT_FOUND','项目不存在')
        try:
            records=TypeAdapter(list[GoalRecord]).validate_python(state.meta.get('_goal_records',[]),strict=True)
            if len(records)>200 or len({r.request_id for r in records})!=len(records):raise ValueError('Invalid history')
            name=state.meta.get('project_name');duration=state.meta.get('target_duration')
            if records and (records[-1].project_name!=name or records[-1].target_duration!=duration):raise ValueError('Invalid checkpoint')
            data={'project_id':project,'project_name':name,'target_duration':duration,'story_excerpt':str(state.meta.get('idea') or state.meta.get('user_textbox_input') or '')[:2000],'style':state.meta.get('style') or 'realistic','video_ratio':state.meta.get('video_ratio') or '9:16','revision':len(records),'running':project in self.engine._active_sessions}
            token=digest({'goal':{k:v for k,v in data.items() if k!='running'},'history':[r.model_dump() for r in records]})
            return state,records,GoalView(**data,expected_token=token)
        except Exception:fail(503,'PROJECT_GOAL_UNAVAILABLE','项目目标记录暂不可读，原文件保留，请检查本地服务')
    def read(self,project):
        with self.engine._state_lock:return self.state(project)[2]
    def save(self,project,payload):
        with self.engine._state_lock:
            state,records,view=self.state(project)
            old=next((r for r in records if r.request_id==payload.request_id),None)
            if old:
                if old.model_dump(exclude={'saved_at'})!=payload.model_dump():fail(409,'GOAL_REQUEST_REUSED','请求编号已用于另一份目标')
                return view
            if view.running:fail(409,'PROJECT_RUNNING','项目正在执行，请完成后再编辑目标')
            if view.expected_token!=payload.expected_token:fail(409,'PROJECT_GOAL_CHANGED','项目目标或配置已变化，请先重新读取并核对')
            if len(records)>=200:fail(422,'PROJECT_GOAL_LIMIT','目标修订达到上限，原记录保留')
            record=GoalRecord(**payload.model_dump(),saved_at=datetime.now(timezone.utc).isoformat())
            updates={'project_name':payload.project_name,'target_duration':payload.target_duration,'_goal_records':[r.model_dump() for r in records]+[record.model_dump()]}
            try:self.engine.update_session_meta(project,updates,tuple(updates))
            except Exception:fail(503,'PROJECT_GOAL_SAVE_UNCONFIRMED','保存结果尚未确认，请重新读取目标核对，输入请保留')
            return self.state(project)[2]
