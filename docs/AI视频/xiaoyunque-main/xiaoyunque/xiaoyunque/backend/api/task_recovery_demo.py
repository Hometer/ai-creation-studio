"""Isolated persistent task fixtures. Artificial providers; genuine free ffmpeg output."""
import argparse
from pathlib import Path
from uuid import uuid4
import uvicorn
from api.initial_draft_demo import create_initial_demo, ArtificialProvider
from api.release_media_demo import create_release_demo, ArtificialMediaProvider
from api.composition_demo import create_composition_demo
from api.dependencies import get_workflow_engine
from api.routers.assets import get_asset_library, get_media_archive
from api.routers import initial_drafts, release_media, content_revisions, co_creation, creation_candidates
from api.schemas.initial_drafts import InitialPrepare, InitialConfirm, InitialSelect
from api.schemas.release_media import MediaPrepare
from api.schemas.compositions import CompositionPrepare, CompositionConfirm
from core.initial_drafts import InitialDrafts
from core.release_media import ReleaseMedia
from core.generation_plans import GenerationPlans
from core.creation_candidates import CreationCandidates
from api.creation_candidate_demo import ArtificialCandidateProvider


def create_task_recovery_demo(directory):
    directory = Path(directory)
    # Each factory only adds its own missing files. Use the final engine for every live service.
    create_release_demo(directory)
    create_initial_demo(directory)
    app, films = create_composition_demo(directory)
    engine = app.dependency_overrides[get_workflow_engine]()
    library = app.dependency_overrides[get_asset_library]()
    archive = app.dependency_overrides[get_media_archive]()
    drafts = InitialDrafts(engine, ArtificialProvider())
    media = ReleaseMedia(engine, library, archive, GenerationPlans(engine, library, archive), ArtificialMediaProvider(directory))
    app.include_router(initial_drafts.router)
    app.include_router(release_media.router)
    app.include_router(content_revisions.router)
    app.include_router(co_creation.router)
    app.include_router(creation_candidates.router)
    app.dependency_overrides[initial_drafts.service] = lambda: drafts
    app.dependency_overrides[release_media.service] = lambda: media
    app.dependency_overrides[creation_candidates.service] = lambda: CreationCandidates(engine, ArtificialCandidateProvider())

    if not drafts.workspace('initial-demo').jobs:
        w = drafts.workspace('initial-demo')
        j = drafts.prepare('initial-demo', InitialPrepare(request_id=uuid4().hex, expected_workspace=w.expected_workspace, kind='script_generation', prompt='人工灯塔样例，不调用真实模型'))
        drafts.confirm('initial-demo', j.job_id, InitialConfirm(request_id=uuid4().hex, plan_hash=j.plan_hash, acknowledged=True, material_reviewed=True))
        done = drafts.advance('initial-demo', j.job_id)
        drafts.select('initial-demo', j.job_id, InitialSelect(request_id=uuid4().hex, expected_workspace=drafts.workspace('initial-demo').expected_workspace, result_digest=done.result_digest, acknowledged=True))
        w = drafts.workspace('initial-demo')
        drafts.prepare('initial-demo', InitialPrepare(request_id=uuid4().hex, expected_workspace=w.expected_workspace, kind='storyboard', prompt='人工分镜候选，等待确认'))
    if not media.workspace('media-demo').jobs:
        w = media.workspace('media-demo')
        media.prepare('media-demo', MediaPrepare(request_id=uuid4().hex, kind='image', source_id=w.images[0].reference.reference_id, segment_id='seg-1', expected_workspace=w.expected_workspace, prompt='人工参考图计划，等待确认', preserve='保留原素材'))
    if not films.workspace('composition-demo').jobs:
        w = films.workspace('composition-demo')
        options = {o.name:o for o in w.options}
        clips = [dict(segment_id=segment, asset_id=options[name].asset_id, version_id=options[name].version_id) for segment,name in [('blue','人工蓝色有声音'),('gold','人工黄色静音')]]
        j = films.prepare('composition-demo', CompositionPrepare(request_id=uuid4().hex, expected_workspace=w.expected_workspace, clips=clips, keep_stale=[]))
        films.confirm('composition-demo', j.job_id, CompositionConfirm(request_id=uuid4().hex, plan_hash=j.plan_hash, acknowledged=True))
        films.advance('composition-demo', j.job_id)
    @app.get('/api/sessions')
    def saved_local_sessions():
        return {'sessions':engine.list_saved_sessions()}
    @app.get('/api/tasks')
    def empty_legacy_pipeline():
        return {'tasks': []}
    @app.get('/api/sandbox/tasks')
    def empty_legacy_sandbox():
        return {'tasks': []}
    app.title = '首版任务汇总 · 人工接口样例，无真实模型或收费'
    return app


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--port', type=int, default=8021)
    args = p.parse_args()
    uvicorn.run(create_task_recovery_demo(args.directory.resolve()), host='127.0.0.1', port=args.port, access_log=False)

if __name__ == '__main__':
    main()
