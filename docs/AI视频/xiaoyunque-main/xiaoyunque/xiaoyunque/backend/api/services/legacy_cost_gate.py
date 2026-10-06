"""Fail closed before any unpriced legacy model action in the production app."""
import re
from fastapi import APIRouter
from starlette.responses import JSONResponse
from api.schemas.assets import Contract
from typing import Literal

OLD_ACTIONS=[re.compile(p) for p in [r'/api/project/[^/]+/execute/[^/]+',r'/api/project/[^/]+/intervene',r'/api/sandbox/(llm|vlm|t2i|i2i|video)',r'/api/pipelines/[^/]+/tasks']]
MESSAGE='旧入口尚无完整调用次数与费用上界，本次未执行。请在项目中准备剧本／分镜或媒体候选计划，逐次核对费用后生成；合成请从选用片段入口操作。'
class LegacyCostGate:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']=='http' and scope['method']=='POST' and any(pattern.fullmatch(scope['path'].rstrip('/')) for pattern in OLD_ACTIONS):
            await JSONResponse({'error':{'code':'LEGACY_COST_PLAN_REQUIRED','message':MESSAGE},'detail':MESSAGE},status_code=409,headers={'Cache-Control':'no-store'})(scope,receive,send)
            return
        await self.app(scope,receive,send)
class CostPolicy(Contract):
    single_limit_fen:Literal[500]=500
    round_limit_fen:Literal[2000]=2000
    legacy_execution_allowed:Literal[False]=False
    reason:str=MESSAGE
    billed_amount_known:Literal[False]=False
router=APIRouter(tags=['首版费用入口'])
@router.get('/api/cost-policy',response_model=CostPolicy)
def policy():return CostPolicy()
