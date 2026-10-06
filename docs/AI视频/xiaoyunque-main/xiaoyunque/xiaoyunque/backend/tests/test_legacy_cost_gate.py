"""Production ASGI boundary refuses unpriced actions before route/SDK effects."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.services.legacy_cost_gate import LegacyCostGate,router
from api.app import app as production
PAID=['/api/project/saved/execute/script_generation','/api/project/saved/execute/character_design','/api/project/saved/execute/storyboard','/api/project/saved/execute/reference_generation','/api/project/saved/execute/video_generation','/api/project/saved/execute/post_production','/api/project/saved/intervene','/api/sandbox/llm','/api/sandbox/vlm','/api/sandbox/t2i','/api/sandbox/i2i','/api/sandbox/video','/api/pipelines/standard/tasks','/api/pipelines/action_transfer/tasks','/api/pipelines/digital_human/tasks','/api/pipelines/quick_create/tasks']
@pytest.mark.parametrize('path',PAID)
def test_production_old_paid_actions_refuse_forged_confirmation(path):
    c=TestClient(production,raise_server_exceptions=False)
    r=c.post(path+'/',json={'acknowledged':True,'material_reviewed':True,'budget_fen':500,'round_limit_fen':2000,'api_key':'private-fixture'},headers={'X-Cost-Confirmed':'true'})
    assert r.status_code==409 and r.json()['error']['code']=='LEGACY_COST_PLAN_REQUIRED' and r.headers['cache-control']=='no-store'
    assert 'private-fixture' not in r.text

def test_no_body_or_sdk_side_effect_and_free_read_continuation_is_retained():
    a=FastAPI();a.add_middleware(LegacyCostGate);a.include_router(router);calls=[]
    @a.post('/api/sandbox/llm')
    async def expensive():calls.append('sdk-init');return {}
    @a.post('/api/project/saved/continue')
    async def local():calls.append('local-transition');return {'status':'ready'}
    @a.get('/api/sandbox/history')
    async def history():return {'history':['saved-result']}
    c=TestClient(a)
    assert c.post('/api/sandbox/llm',content=b'{malformed private').status_code==409 and not calls
    assert c.get('/api/sandbox/history').json()['history']==['saved-result']
    assert c.post('/api/project/saved/continue').json()['status']=='ready' and calls==['local-transition']
    p=c.get('/api/cost-policy');assert p.status_code==200 and p.json()['legacy_execution_allowed'] is False and p.json()['billed_amount_known'] is False

def test_native_confirm_paths_are_not_blocked_by_legacy_gate():
    a=FastAPI();a.add_middleware(LegacyCostGate);calls=[]
    @a.post('/api/project/saved/initial-drafts/job/confirm')
    async def native():calls.append('native-approval-validation');return {'validation':'required'}
    c=TestClient(a);assert c.post('/api/project/saved/initial-drafts/job/confirm').json()=={'validation':'required'} and calls==['native-approval-validation']
