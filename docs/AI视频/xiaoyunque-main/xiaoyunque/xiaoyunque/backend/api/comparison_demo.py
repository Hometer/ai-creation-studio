"""Isolated artificial comparison samples. No provider execution routes."""
import argparse
import copy
from pathlib import Path
import uvicorn
from api.manual_edit_demo import create_manual_demo
from api.dependencies import get_workflow_engine
from api.routers.assets import router as assets_router
from api.routers.generation_plans import router as plans_router


def create_comparison_demo(directory: Path):
    app = create_manual_demo(directory)
    app.title = '两版文字对比 · 人工样例，不调用模型'
    app.include_router(assets_router)
    app.include_router(plans_router)
    engine = app.dependency_overrides[get_workflow_engine]()
    project = 'comparison-demo'
    if not (directory / 'sessions' / f'{project}.json').exists():
        engine.create_session(project, {'idea': '两版文字对比 · 人工样例，不调用模型'})
        origin = engine.get_state('manual-edit-demo')
        state = engine.get_state(project)
        state.artifacts = copy.deepcopy(origin.artifacts)
        state.status = copy.deepcopy(origin.status)
        state.current_stage = origin.current_stage
        for stage in ('script_generation', 'storyboard'):
            state.artifacts[stage]['session_id'] = project
        script = state.artifacts['script_generation']
        script['制作批次'] = '1'
        script['待补说明'] = None
        engine.save_session_to_disk(project)

        first = copy.deepcopy(script)
        first.update(title='最后一班渡船 · 人工第一版', logline='青年等待渡船，守望者独自守着灯塔。')
        second = copy.deepcopy(first)
        second.update(title='最后一班渡船 · 人工第二版', logline='青年和守望者共同点亮灯塔，迎接最后一班渡船。')
        second['制作批次'] = 1
        second['新增说明'] = None
        del second['待补说明']
        # Distinct durable records include a repeated snapshot, to demonstrate no-diff.
        for content in (first, second, first, second):
            previous = copy.deepcopy(state.artifacts)
            state.artifacts['script_generation'] = copy.deepcopy(content)
            engine.save_session_to_disk(project, archive_text=True, expected_text=previous)

        board = copy.deepcopy(state.artifacts['storyboard'])
        board['episodes'][0]['segments'][0]['shots'][0]['dialogue'] = '和你一起等最后一班渡船。'
        engine.update_artifact(project, 'storyboard', copy.deepcopy(board))
        board['episodes'][0]['segments'][0] = {'segment_id': 'seg-new', 'visual_prompt': '灯塔旁的新木桥。',
            'shots': [{'shot_number': 1, 'content': '青年把灯交给守望者。', 'dialogue': '我们一起点灯。', 'duration': 5}]}
        engine.update_artifact(project, 'storyboard', board)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8007)
    args = parser.parse_args()
    uvicorn.run(create_comparison_demo(args.directory.resolve()), host='127.0.0.1', port=args.port, access_log=False)

if __name__ == '__main__':
    main()
