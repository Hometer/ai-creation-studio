"""Temporary files and artificial credentials only; never display real local secrets."""
import copy
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import config
from config_secrets import SECRET_ENV, read_env
from api.routers.configuration import router
from api.errors import register_error_handlers

@pytest.fixture
def local(tmp_path,monkeypatch):
    for name, value in vars(config.Config).items():
        if name.isupper():monkeypatch.setattr(config.Config,name,copy.deepcopy(value))
    monkeypatch.setattr(config,'CONFIG_PATH',tmp_path/'config.yaml')
    monkeypatch.setattr(config,'CONFIG_EXAMPLE_PATH',tmp_path/'absent-example.yaml')
    for key in SECRET_ENV.values():monkeypatch.delenv(key,raising=False)
    return tmp_path


def values():
    v=copy.deepcopy(config.DEFAULT_CONFIG)
    for n,path in enumerate(SECRET_ENV):config._set(v,path,f'artificial-fixture-{n}')
    return v


def test_all_secrets_only_env_masked_and_restart_load(local):
    v=values();assert config.save_config(v)==v
    disk=(local/'config.yaml').read_text();env=local/'.env'
    assert stat.S_IMODE(env.stat().st_mode)==0o600
    for path,key in SECRET_ENV.items():
        secret=config._get(v,path);assert secret not in disk and secret not in repr(config.redact_config(config.load_config()))
        assert read_env(env)[1][key]==secret
    assert config.load_config()==v
    # A fresh Python process reads the same files, no process-global environment mutation.
    code="import config,json; from pathlib import Path; config.CONFIG_PATH=Path(__import__('sys').argv[1]);print(json.dumps(config.redact_config(config.load_config())))"
    r=subprocess.run([sys.executable,'-c',code,str(local/'config.yaml')],capture_output=True,text=True,check=True)
    assert json.loads(r.stdout)['api_providers']['dashscope']['api_key']==config.SECRET_MASK
    assert 'artificial-fixture' not in r.stdout+r.stderr


def test_preserve_unrelated_env_comments_and_no_execution(local):
    env=local/'.env';original='# untouched\nOTHER="$(never-execute)"\nexport DASHSCOPE_API_KEY=\'artificial-old\'\nTAIL=no-newline'
    env.write_text(original);v=config.load_config();assert config._get(v,'api_providers.dashscope.api_key')=='artificial-old'
    config.save_config(config.merge_config_update(v,{'server':{'port':8999},'api_providers':{'dashscope':{'api_key':config.SECRET_MASK}}}))
    text=env.read_text();assert '# untouched\nOTHER="$(never-execute)"\nTAIL=no-newline\n' in text
    assert read_env(env)[1]['DASHSCOPE_API_KEY']=='artificial-old'
    assert config.load_config()['server']['port']==8999


def test_legacy_read_no_automatic_migration_and_explicit_clear(local):
    v=values();(local/'config.yaml').write_text(config.yaml.safe_dump(v))
    assert config.load_config()==v and not (local/'.env').exists()
    config.save_config(config.merge_config_update(v,{'api_providers':{'dashscope':{'api_key':''}}}))
    assert config.load_config()['api_providers']['dashscope']['api_key']==''
    assert all('artificial-fixture' not in line for line in (local/'config.yaml').read_text().splitlines())


@pytest.mark.parametrize('text',[
 'DASHSCOPE_API_KEY=artificial-a\nDASHSCOPE_API_KEY=artificial-b\n',
 'DASHSCOPE_API_KEY "artificial"',
 'DASHSCOPE_API_KEY="bad-quote',
 "DASHSCOPE_API_KEY='bad-quote",
 'DASHSCOPE_API_KEY="line\\nsecret"',
 'DASHSCOPE_API_KEY='+('x'*4097),
 'DASHSCOPE_API_KEY="$(bad) with spaces"',
])
def test_bad_env_preserved_no_overwrite(local,text):
    env=local/'.env';env.write_text(text)
    with pytest.raises(Exception):config.load_config()
    with pytest.raises(Exception):config.save_config(values())
    assert env.read_text()==text and not (local/'config.yaml').exists()


@pytest.mark.parametrize('target',['.env','config.yaml','.env.lock'])
def test_symlink_refused(local,target):
    outside=local/'outside';outside.write_text('untouched');(local/target).symlink_to(outside)
    with pytest.raises(Exception):config.save_config(values())
    assert outside.read_text()=='untouched'


def test_oversized_store_refused(local):
    env=local/'.env';env.write_bytes(b'x'*(128*1024+1))
    before=env.read_bytes()
    with pytest.raises(Exception):config.save_config(values())
    assert env.read_bytes()==before


@pytest.mark.parametrize('prior',[False,True])
def test_yaml_replace_failure_rolls_back_env(local,monkeypatch,prior):
    if prior:config.save_config(values())
    before={p.name:p.read_bytes() for p in local.iterdir() if p.name!='.env.lock'}
    replace=os.replace
    def broken(source,destination):
        if Path(destination)==local/'config.yaml':raise OSError('private credential must never appear')
        return replace(source,destination)
    monkeypatch.setattr('config_secrets.os.replace',broken)
    with pytest.raises(OSError):config.save_config(config.merge_config_update(values(),{'api_providers':{'dashscope':{'api_key':'artificial-new'}}}))
    assert {p.name:p.read_bytes() for p in local.iterdir() if p.name!='.env.lock'}==before


def test_environment_priority_and_platform_not_copied(local,monkeypatch):
    config.save_config(values());monkeypatch.setenv('DASHSCOPE_API_KEY','artificial-platform')
    v=config.load_config();assert v['api_providers']['dashscope']['api_key']=='artificial-platform'
    config.save_config(v)
    assert 'artificial-platform' not in (local/'.env').read_text()+(local/'config.yaml').read_text()
    with pytest.raises(ValueError):config.save_config(values())


def test_http_masked_and_write_failure_safe(local,monkeypatch):
    monkeypatch.setattr(config.Config,'CONFIG',config.DEFAULT_CONFIG)
    app=FastAPI();register_error_handlers(app);app.include_router(router);c=TestClient(app,raise_server_exceptions=False)
    r=c.put('/api/config',json={'values':values()});assert r.status_code==200 and 'artificial-fixture' not in r.text
    assert c.get('/api/config').json()['config']['api_providers']['dashscope']['api_key']==config.SECRET_MASK
    monkeypatch.setattr(config.Config,'update_config',lambda v:(_ for _ in ()).throw(OSError('artificial-private-secret')))
    r=c.put('/api/config',json={'values':{}})
    assert r.status_code==503 and r.json()['error']['code']=='CONFIG_SAVE_UNCONFIRMED' and 'artificial-private-secret' not in r.text


def test_temp_secret_names_are_ignored(local):
    from config_secrets import stage_bytes
    staged=stage_bytes(local/'.env',b'artificial')
    assert staged.name.startswith('.env.')
    staged.unlink()


def test_invalid_utf8_and_yaml_do_not_expose_document(local):
    (local/'.env').write_bytes(b'DASHSCOPE_API_KEY=artificial-private\xff')
    with pytest.raises(ValueError) as caught:config.load_config()
    assert 'artificial-private' not in str(caught.value)
    (local/'.env').write_text('')
    (local/'config.yaml').write_text('api_key: [artificial-private')
    with pytest.raises(ValueError) as caught:config.load_config()
    assert 'artificial-private' not in str(caught.value) and caught.value.__suppress_context__
