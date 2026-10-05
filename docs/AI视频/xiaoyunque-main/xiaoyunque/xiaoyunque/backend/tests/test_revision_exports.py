"""Actual local HTTP attachments, snapshot completeness and non-writing behavior."""
import copy
import hashlib
import json
import uuid
import pytest
from fastapi.testclient import TestClient
from api.export_demo import create_export_demo
from api.dependencies import get_workflow_engine
from core.content_revisions import digest, STAGES
from core.orchestrator import WorkflowEngine
from core.revision_exports import RevisionExports, VERSION_KEYS

P = 'comparison-demo'
URL = f'/api/project/{P}/content-revisions'


@pytest.fixture
def setup(tmp_path):
    app = create_export_demo(tmp_path)
    return TestClient(app, raise_server_exceptions=False), app.dependency_overrides[get_workflow_engine](), tmp_path


def workspace(c, project=P):
    r = c.get(f'/api/project/{project}/content-revisions')
    assert r.status_code == 200, r.text
    return r.json()


def download(c, project=P, fmt='json', token=None):
    return c.get(f'/api/project/{project}/content-revisions/export', params={
        'format': fmt, 'expected_token': token or workspace(c, project)['token']})


def files(directory):
    return {str(p): p.read_bytes() for p in directory.rglob('*') if p.is_file()}


@pytest.mark.parametrize('fmt', ['json', 'text'])
def test_real_attachment_headers_hash_determinism_and_no_writes(setup, monkeypatch, fmt):
    c, e, d = setup
    before = files(d)
    state = copy.deepcopy(e.get_state(P).__dict__)
    monkeypatch.setattr(e, 'save_session_to_disk', lambda *a, **k: pytest.fail('export saved state'))
    w = workspace(c)
    r = download(c, fmt=fmt)
    assert r.status_code == 200, r.text
    extension = 'json' if fmt == 'json' else 'txt'
    assert r.headers['content-disposition'] == f'attachment; filename="text-versions-{P}-{w["token"][:12]}.{extension}"'
    assert r.headers['content-type'].startswith('application/json' if fmt == 'json' else 'text/plain; charset=utf-8')
    assert r.headers['content-length'] == str(len(r.content))
    assert r.headers['x-export-bytes'] == str(len(r.content))
    assert r.headers['x-export-sha256'] == hashlib.sha256(r.content).hexdigest()
    assert r.headers['x-revision-token'] == w['token'] and r.headers['x-export-versions'] == '8'
    assert r.headers['x-content-type-options'] == 'nosniff' and r.headers['cache-control'] == 'no-store'
    assert download(c, fmt=fmt).content == r.content
    assert files(d) == before and e.get_state(P).__dict__ == state


def test_json_contains_full_current_history_types_and_unknown_structure(setup):
    c, e, _ = setup
    result = download(c).json()
    assert result['format'] == 'text-revisions' and result['schema_version'] == 1
    assert len(result['versions']) == 8
    for stage in STAGES:
        current = result['current'][stage]
        assert current['content'] == e.get_state(P).artifacts[stage] and current['digest'] == digest(current['content'])
        assert current['version_id'] == next(v['version_id'] for v in reversed(result['versions']) if v['stage'] == stage)
    for original, exported in zip(e.get_state(P).content_history['versions'], result['versions']):
        assert exported == {k: original[k] for k in VERSION_KEYS}
    assert type(result['versions'][0]['content']['制作批次']) is str
    assert type(result['current']['script_generation']['content']['制作批次']) is int
    assert result['versions'][0]['content']['待补说明'] is None
    assert '待补说明' not in result['current']['script_generation']['content']
    assert result['current']['storyboard']['content']['episodes'][0]['unknown'] == '人工章节附加字段'


def test_text_has_every_snapshot_readable_values_and_complete_json(setup):
    c, _, _ = setup
    structured = download(c).json()
    text = download(c, fmt='text').content.decode('utf-8')
    assert text.startswith(f'创作工作室 · 文字版本导出\n项目：{P}\n导出依据：{structured["token"]}\n')
    assert text.count('===== 归档 v') == 8 and text.count('===== 当前') == 2
    assert '原入口更新归档' in text and '完整结构（保留附加字段与类型）' in text
    from core.content_revisions import encoded
    for record in list(structured['current'].values()) + structured['versions']:
        assert encoded(record['content']).decode('utf-8') in text
    assert '青年和守望者共同点亮灯塔' in text


@pytest.mark.parametrize('project', ['manual-edit-demo', 'revision-empty'])
def test_no_history_current_and_empty_are_exported_without_creating_baselines(setup, project):
    c, e, d = setup
    before = files(d)
    result = download(c, project).json()
    assert result['versions'] == [] and result['untracked_stages'] == []
    assert all(s['version_id'] is None for s in result['current'].values())
    assert result['current']['script_generation']['content'] == e.get_state(project).artifacts.get('script_generation', {})
    assert download(c, project, 'text').status_code == 200 and files(d) == before


def test_untracked_current_is_kept_separately_from_archive(setup):
    c, e, d = setup
    s = e.get_state(P)
    old = copy.deepcopy(s.content_history)
    s.artifacts['script_generation']['title'] = '未归档的持久正文'
    e.save_session_to_disk(P)
    before = files(d)
    result = download(c).json()
    assert result['untracked_stages'] == ['script_generation']
    assert result['current']['script_generation']['version_id'] is None
    assert result['current']['script_generation']['content']['title'] == '未归档的持久正文'
    assert result['versions'] == [{k: v[k] for k in VERSION_KEYS} for v in old['versions']]
    assert '未归档变化' in download(c, fmt='text').text and files(d) == before


def test_preserves_restore_relationship_and_excludes_private_session_and_request_fields(setup):
    c, e, d = setup
    w = workspace(c)
    payload = {'expected_token': w['token'], 'stage': 'script_generation', 'restore_version_id': w['versions'][0]['version_id']}
    preview = c.post(URL + '/preview', json=payload).json()
    saved = c.post(URL, json={**payload, 'preview_token': preview['preview_token'], 'request_id': uuid.uuid4().hex, 'acknowledged': True})
    assert saved.status_code == 201
    s = e.get_state(P)
    s.meta['private_config'] = 'root_private_marker'
    s.artifacts['video_generation']['internal'] = 'non_text_marker'
    s.stage_progress['private'] = {'note': 'progress_private_marker'}
    e.save_session_to_disk(P)
    before = files(d)
    for fmt in ['json', 'text']:
        r = download(c, fmt=fmt)
        assert r.status_code == 200
        for forbidden in ['root_private_marker', 'non_text_marker', 'progress_private_marker', 'request_id', 'request_digest']:
            assert forbidden not in r.text
    versions = download(c).json()['versions']
    assert versions[-1]['source'] == 'restore' and versions[-1]['restored_from'] == versions[0]['version_id']
    assert files(d) == before


@pytest.mark.parametrize('bad', ['missing_project', 'other_token', 'expired', 'missing_format', 'bad_format', 'missing_token', 'bad_token', 'path_id'])
def test_project_query_and_revision_boundaries_refuse_without_writes(setup, bad):
    c, _, d = setup
    w = workspace(c)
    params = {'format': 'json', 'expected_token': w['token']}
    url = URL + '/export'
    status = 422
    if bad == 'missing_project':
        url = '/api/project/not-found/content-revisions/export'; status = 404
    elif bad == 'other_token':
        params['expected_token'] = workspace(c, 'manual-edit-demo')['token']; status = 409
    elif bad == 'expired':
        params['expected_token'] = '0' * 64; status = 409
    elif bad == 'missing_format': del params['format']
    elif bad == 'bad_format': params['format'] = 'private/path.zip'
    elif bad == 'missing_token': del params['expected_token']
    elif bad == 'bad_token': params['expected_token'] = 'private/path'
    else: url = '/api/project/unsafe%22name/content-revisions/export'
    before = files(d)
    r = c.get(url, params=params)
    assert r.status_code == status and 'error' in r.json() and 'private/path' not in r.text
    assert 'content-disposition' not in r.headers and files(d) == before


@pytest.mark.parametrize('damage', ['json', 'history', 'future', 'external', 'unsupported'])
def test_damage_or_external_write_does_not_export_cached_content(setup, damage):
    c, _, d = setup
    token = workspace(c)['token']
    path = d / 'sessions' / f'{P}.json'
    data = json.loads(path.read_text())
    status = 503
    if damage == 'json': path.write_text('{private_path')
    else:
        if damage == 'history': data['content_history']['versions'][0]['digest'] = '0' * 64
        elif damage == 'future': data['schema_version'] = 99
        elif damage == 'external': data['artifacts']['script_generation']['title'] = '外部正文'
        else:
            data['artifacts']['script_generation'] = ['旧非字典结构']
            status = 422
        path.write_text(json.dumps(data))
    # Unsupported content is tested with a fresh engine, so it is not a cache conflict.
    if damage == 'unsupported':
        from core.asset_library import AssetLibraryError
        with pytest.raises(AssetLibraryError) as exc: RevisionExports(WorkflowEngine(str(d / 'sessions'))).download(P, token, 'json')
        assert exc.value.status == status
        return
    before = files(d)
    r = download(c, token=token)
    assert r.status_code == status and 'private_path' not in r.text and files(d) == before


@pytest.mark.parametrize('fmt', ['json', 'text'])
def test_response_limit_is_in_bytes_and_never_truncates_or_writes(setup, monkeypatch, fmt):
    c, _, d = setup
    before = files(d)
    monkeypatch.setattr('core.revision_exports.MAX_EXPORT_BYTES', 100)
    r = download(c, fmt=fmt)
    assert r.status_code == 422 and r.json()['error']['code'] == 'EXPORT_LIMIT' and files(d) == before


def test_more_than_editable_field_limit_can_still_be_preserved(setup):
    _, e, d = setup
    s = e.get_state('revision-empty')
    s.artifacts['script_generation'] = {'episodes': [{'content': f'文字{i}'} for i in range(501)]}
    e.save_session_to_disk('revision-empty')
    token = digest({'current': {stage: s.artifacts.get(stage, {}) for stage in STAGES}, 'history': {'schema_version': 1, 'versions': []}})
    before = files(d)
    r = RevisionExports(e).download('revision-empty', token, 'json')
    assert len(json.loads(r.body)['current']['script_generation']['content']['episodes']) == 501 and files(d) == before


def test_types_unicode_linebreaks_empty_values_and_deep_unknown_fields_are_preserved(setup):
    _, e, _ = setup
    s = e.get_state('revision-empty')
    content = {'title': '中文\n第二行 😀', 'blank': '', 'zero': 0, 'flag': False, 'nil': None, 'list': [], 'object': {}}
    nested = content
    for _ in range(12): nested['deep'] = {}; nested = nested['deep']
    nested['custom'] = '附加文字'
    s.artifacts['script_generation'] = content
    e.save_session_to_disk('revision-empty')
    token = workspace(setup[0], 'revision-empty')['token']
    assert json.loads(RevisionExports(e).download('revision-empty', token, 'json').body)['current']['script_generation']['content'] == content
    text = RevisionExports(e).download('revision-empty', token, 'text').body.decode('utf-8')
    assert '中文\n第二行 😀' in text and 'false' in text and '附加文字' in text


def test_actual_engine_restart_and_running_reads_keep_identical_files(setup):
    c, _, d = setup
    token = workspace(c)['token']
    expected = {fmt: download(c, fmt=fmt).content for fmt in ['json', 'text']}
    fresh = WorkflowEngine(str(d / 'sessions'))
    fresh._active_sessions.add(P)
    before = files(d)
    for fmt in ['json', 'text']:
        assert RevisionExports(fresh).download(P, token, fmt).body == expected[fmt]
    assert files(d) == before


@pytest.mark.parametrize('fmt', ['json', 'text'])
def test_exact_byte_boundary_accepts_and_one_byte_less_rejects(setup, monkeypatch, fmt):
    c, _, d = setup
    expected = download(c, fmt=fmt).content
    before = files(d)
    monkeypatch.setattr('core.revision_exports.MAX_EXPORT_BYTES', len(expected))
    assert download(c, fmt=fmt).content == expected
    monkeypatch.setattr('core.revision_exports.MAX_EXPORT_BYTES', len(expected)-1)
    assert download(c, fmt=fmt).status_code == 422 and files(d) == before


def test_serialization_has_detached_snapshots_after_lock_is_released(setup, monkeypatch):
    c, e, _ = setup
    token = workspace(c)['token']
    old = copy.deepcopy(e.get_state(P).artifacts['script_generation'])
    from core.content_revisions import encoded
    fired = False
    def encode_after_concurrent_write(value):
        nonlocal fired
        if not fired and isinstance(value, dict) and value.get('format') == 'text-revisions':
            fired = True
            e.update_artifact(P, 'script_generation', {'title': '完成读取之后的更新'})
        return encoded(value)
    monkeypatch.setattr('core.revision_exports.encoded', encode_after_concurrent_write)
    result = RevisionExports(e).download(P, token, 'json')
    assert fired and json.loads(result.body)['current']['script_generation']['content'] == old
    assert result.headers['X-Revision-Token'] == token and e.get_state(P).artifacts['script_generation']['title'] == '完成读取之后的更新'
