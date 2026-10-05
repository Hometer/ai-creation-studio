"""Local uploads and append-only references; real bytes, no model or user data."""

import io
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from core import asset_library
from core.asset_library import AssetLibrary
from test_media_archive import fixture, video_bytes  # noqa: F401


def png(color="steelblue"):
    stream = io.BytesIO()
    Image.new("RGB", (160, 90), color).save(stream, format="PNG")
    return stream.getvalue()


def upload(client, content=None, *, upload_id="a" * 32, filename="人工素材.png",
           mime="image/png", project="media-proof", **fields):
    return client.post(f"/api/project/{project}/assets/upload", data={
        "upload_id": upload_id, "name": "人工素材", "definition": "本地人工验收，无模型结果", **fields,
    }, files={"file": (filename, png() if content is None else content, mime)})


def test_upload_pins_history_and_does_not_change_original_project(fixture):
    client, library, archive, engine, _, _ = fixture
    original = engine.get_status_snapshot("media-proof")
    asset = upload(client).json()
    first = asset["versions"][0]
    assert first["source"] == {"type": "upload", "project_id": "media-proof", "upload_id": "a" * 32}
    ref = client.post("/api/asset-references", json={"project_id": "media-proof", "asset_id": asset["asset_id"], "version_id": first["version_id"], "segment_id": "seg-1", "shot_number": 1}).json()
    changed = upload(client, png("gold"), upload_id="b" * 32, asset_id=asset["asset_id"]).json()
    assert len(changed["versions"]) == 2 and changed["versions"][0] == first
    assert library.read().references[ref["reference_id"]].version_id == first["version_id"]
    assert client.get(f'/api/assets/{asset["asset_id"]}/usage').json()[0]["has_newer_version"]
    url = f'/api/assets/{asset["asset_id"]}/versions/{first["version_id"]}/media'
    assert client.get(url).content == png()
    assert client.get(url).headers["x-content-type-options"] == "nosniff"
    assert engine.get_status_snapshot("media-proof") == original
    restored = AssetLibrary(library.directory).read()
    assert len(restored.assets[asset["asset_id"]].versions) == 2
    assert not list(archive.directory.glob(".*upload*")) and not list(archive.directory.glob(".staging-*"))


def test_upload_idempotent_even_after_a_later_version(fixture):
    client, library, *_ = fixture
    first = upload(client).json()
    changed = upload(client, png("gold"), upload_id="b" * 32, asset_id=first["asset_id"]).json()
    assert upload(client).json() == changed
    assert upload(client, png("gold"), upload_id="b" * 32, asset_id=first["asset_id"]).json() == changed
    assert len(library.read().assets) == 1


@pytest.mark.parametrize("changes", [{"name": "不同名字"}, {"definition": "不同描述"}, {"project": "other-project"}, {"content": b"different", "filename": "bad.png"}])
def test_reused_upload_id_cannot_change_content_or_metadata(fixture, changes):
    client, library, *_ = fixture
    upload(client)
    before = library.path.read_bytes()
    response = upload(client, **changes)
    assert response.status_code in {409, 415}
    assert library.path.read_bytes() == before


def test_reused_upload_cannot_change_create_to_append(fixture):
    client, *_ = fixture
    asset = upload(client).json()
    assert upload(client, asset_id=asset["asset_id"]).status_code == 409
    assert upload(client, png("gold"), upload_id="b" * 32, asset_id=asset["asset_id"]).status_code == 201
    assert upload(client, png("gold"), upload_id="b" * 32).status_code == 409


@pytest.mark.parametrize("filename", ["../private.png", "C:\\private.png", "unsafe\x01.png", "a" * 181 + ".png"])
def test_unsafe_upload_names_are_rejected_without_echo(fixture, filename):
    client, library, *_ = fixture
    response = upload(client, filename=filename)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UPLOAD_FILENAME_INVALID"
    assert filename not in response.text and not library.read().assets


@pytest.mark.parametrize("content,filename,mime", [(b"private-sentinel", "file.png", "image/png"), (png(), "file.jpg", "image/jpeg"), (png(), "file.png", "text/plain"), (png(), "file.svg", "image/svg+xml"), (b"", "file.mp4", "video/mp4")])
def test_true_format_and_declared_mime_must_match(fixture, content, filename, mime):
    client, library, archive, *_ = fixture
    response = upload(client, content, filename=filename, mime=mime)
    assert response.status_code == 415
    assert "private-sentinel" not in response.text
    assert not library.read().assets and not list(archive.directory.glob(".staging-*"))


def test_octet_stream_is_verified_by_real_content(fixture):
    client, *_ = fixture
    assert upload(client, mime="application/octet-stream").status_code == 201


@pytest.mark.parametrize("fields", [{"upload_id": "invalid"}, {"name": " "}, {"name": "名" * 101}, {"definition": " "}, {"definition": "字" * 10001}, {"asset_id": "invalid"}])
def test_upload_contract_validation(fixture, fields):
    client, library, *_ = fixture
    assert upload(client, **fields).status_code == 422
    assert not library.read().assets


def test_upload_size_limit_and_missing_project(fixture):
    client, library, *_ = fixture
    assert upload(client, b"x" * (10 * 1024 * 1024 + 1)).status_code == 413
    assert upload(client, project="missing").status_code == 404
    assert not library.read().assets


def test_real_mp4_can_be_read_and_wrong_kind_cannot_append(fixture):
    client, library, archive, engine, _, video = fixture
    asset = upload(client, video.read_bytes(), filename="人工视频.mp4", mime="video/mp4").json()
    first = asset["versions"][0]
    assert asset["kind"] == "video" and first["media"]["duration"] > 0
    assert client.get(f'/api/assets/{asset["asset_id"]}/versions/{first["version_id"]}/media').content == video.read_bytes()
    assert upload(client, upload_id="b" * 32, asset_id=asset["asset_id"]).status_code == 409
    assert len(library.read().assets[asset["asset_id"]].versions) == 1


def test_concurrent_duplicate_upload_is_one_asset_and_version(fixture):
    client, library, *_ = fixture
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: upload(client), range(4)))
    assert all(r.status_code == 201 for r in results)
    assert len({r.json()["asset_id"] for r in results}) == 1
    assert len(library.read().assets) == 1


@pytest.mark.parametrize("committed", [False, True])
def test_failed_storage_never_deletes_a_committed_file(fixture, monkeypatch, committed):
    client, library, archive, *_ = fixture
    original = asset_library.atomic_write_session
    def fail(path, document):
        if committed:
            original(path, document)
        raise OSError("private-sentinel")
    monkeypatch.setattr(asset_library, "atomic_write_session", fail)
    response = upload(client)
    assert response.status_code == 503 and "private-sentinel" not in response.text
    data = library.read()
    assert len(data.assets) == int(committed)
    assert len(list(archive.directory.glob("*.png"))) == int(committed)
    assert not list(archive.directory.glob(".upload-*")) and not list(archive.directory.glob(".staging-*"))


def test_existing_alias_media_and_old_text_library_remain_readable(fixture):
    client, library, archive, engine, *_ = fixture
    text = client.post("/api/assets", json={"kind": "prop", "name": "信件", "definition": "人工道具"}).json()
    state = engine.get_state("media-proof")
    state.artifacts["reference"] = state.artifacts.pop("reference_generation")
    response = client.post("/api/assets/import-media", json={"project_id": "media-proof", "stage": "reference_generation", "item_type": "scenes", "item_id": "seg-1"})
    assert response.status_code == 200
    upload(client)
    assert client.get(f'/api/assets/{text["asset_id"]}').json() == text
