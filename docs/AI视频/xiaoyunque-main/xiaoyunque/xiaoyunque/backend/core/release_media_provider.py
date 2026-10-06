"""Native, bounded mainland media protocol. No SDK retries or response logging."""
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from pydantic import TypeAdapter
from api.schemas.release_media import ProviderId
from core.creation_candidates import CandidateProvider
from core.asset_library import unique_fields
from core.content_revisions import digest
from core.media_archive import IMAGE_LIMIT, VIDEO_LIMIT, sync_directory

HOSTS={
    'dashscope-result-sh.oss-cn-shanghai.aliyuncs.com',
    'dashscope-result-bj.oss-cn-beijing.aliyuncs.com',
    'dashscope-result-wlcb-acdr-1.oss-cn-wulanchabu-acdr-1.aliyuncs.com',
    'dashscope-a717.oss-accelerate.aliyuncs.com',
}
STATES={'PENDING','RUNNING','SUCCEEDED','FAILED','CANCELED','UNKNOWN'}


def result_location(value,kind):
    if not isinstance(value,str) or not 1<=len(value)<=8192 or any(ord(c)<=32 or ord(c)>=127 for c in value):raise ValueError('Invalid result location')
    u=urlsplit(value);suffix=Path(u.path).suffix.lower()
    if u.scheme!='https' or u.hostname not in HOSTS or u.netloc!=u.hostname or u.fragment or suffix not in ({'.mp4'} if kind=='video' else {'.png','.jpg','.jpeg','.webp'}):raise ValueError('Unreviewed result location')
    return u

class ReleaseMediaProvider(CandidateProvider):
    simulation_only=False
    def request(self,transport,method,path,body=None):
        headers={'Authorization':'Bearer '+transport._key,'Accept':'application/json'}
        wire=None
        if body is not None:
            headers.update({'Content-Type':'application/json','X-DashScope-Async':'enable'})
            wire=json.dumps(body,ensure_ascii=False,allow_nan=False).encode()
        with transport._stream(method,transport._host,path,wire,headers,(5,20),30,256*1024) as response:
            raw=b''.join(response.chunks())
        payload=json.loads(raw,object_pairs_hook=unique_fields)
        if not isinstance(payload,dict) or payload.get('code') not in (None,''):raise ValueError('Provider rejected request')
        return payload

    def submit(self,job,body,transport):
        if digest(body)!=job.plan.request_digest or body.get('model')!=job.plan.model:raise ValueError('Request differs from approval')
        path='/api/v1/services/aigc/'+('image2image/image-synthesis' if job.plan.kind=='image' else 'video-generation/video-synthesis')
        payload=self.request(transport,'POST',path,body);output=payload['output']
        task=TypeAdapter(ProviderId).validate_python(output['task_id'],strict=True)
        if output['task_status'] not in {'PENDING','RUNNING'}:raise ValueError('Invalid submit state')
        return task

    def query(self,job,transport):
        task=TypeAdapter(ProviderId).validate_python(job.provider_task_id,strict=True)
        payload=self.request(transport,'GET','/api/v1/tasks/'+task);output=payload['output']
        if output['task_id']!=task or output['task_status'] not in STATES:raise ValueError('Invalid task identity')
        state=output['task_status'];url=None;cost=None
        if state=='SUCCEEDED':
            if job.plan.kind=='image':
                results=output['results']
                if len(results)!=1 or type(payload['usage']['image_count']) is not int or payload['usage']['image_count']!=1:raise ValueError('Unexpected image count')
                url=results[0]['url'];cost=14
            else:
                duration=payload['usage']['duration'];count=payload['usage']['video_count']
                if type(duration) is not int or duration!=job.plan.duration or type(count) is not int or count!=1:raise ValueError('Unexpected video usage')
                url=output['video_url'];cost=duration*(60 if job.plan.resolution=='720P' else 100)
            result_location(url,job.plan.kind)
        return state,url,cost

    def download(self,job,directory,transport):
        u=result_location(job.result_url,job.plan.kind);directory=Path(directory)
        if directory.is_symlink() or directory.parent.is_symlink():raise ValueError('Unsafe result directory')
        directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        destination=directory/('result'+Path(u.path).suffix.lower())
        if destination.is_symlink():raise ValueError('Unsafe result file')
        if destination.exists():return destination
        fd,name=tempfile.mkstemp(dir=directory,prefix='.download-',suffix=destination.suffix)
        temporary=Path(name)
        try:
            path=u.path+('?' +u.query if u.query else '')
            # Result downloads receive neither provider credentials nor arbitrary redirects.
            with os.fdopen(fd,'wb') as output:
                with transport._stream('GET',u.hostname,path,None,{},(5,20),30,VIDEO_LIMIT if job.plan.kind=='video' else IMAGE_LIMIT) as response:
                    for chunk in response.chunks():output.write(chunk)
                output.flush();os.fsync(output.fileno())
            os.link(temporary,destination,follow_symlinks=False);sync_directory(directory)
            return destination
        finally:temporary.unlink(missing_ok=True)
