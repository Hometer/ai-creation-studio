"""Bounded local uploads; never resolve a client path or call a model."""

import os
import tempfile
from email.message import Message
from pathlib import Path
from urllib.parse import unquote

from core.asset_library import AssetLibraryError
from core.media_archive import IMAGE_FORMATS, IMAGE_LIMIT, VIDEO_LIMIT


def archive_upload(file, source, payload, library, archive, asset_id=None):
    filename = file.filename or ""
    disposition = Message()
    disposition["Content-Disposition"] = file.headers.get("content-disposition", "")
    original = unquote(disposition.get_filename() or filename)
    if (not filename or len(filename) > 180 or any(ord(c) < 32 for c in filename)
            or "/" in filename or "\\" in filename
            or any(ord(c) < 32 for c in original) or "/" in original or "\\" in original):
        raise AssetLibraryError(422, "UPLOAD_FILENAME_INVALID", "请使用不含路径或控制字符的文件名（最多 180 字）")
    ext = Path(filename).suffix.lower()
    image_extensions = {e for extensions, _ in IMAGE_FORMATS.values() for e in extensions}
    if ext not in image_extensions | {".mp4"}:
        raise AssetLibraryError(415, "MEDIA_FORMAT_INVALID", "仅支持 JPG、PNG、WebP、BMP 或 MP4")
    kind = "video" if ext == ".mp4" else "image"
    limit = VIDEO_LIMIT if kind == "video" else IMAGE_LIMIT
    if file.size is not None and file.size > limit:
        raise AssetLibraryError(413, "MEDIA_TOO_LARGE", "图片最大 10 MiB，视频最大 100 MiB")
    try:
        archive._directory()
        with tempfile.TemporaryDirectory(prefix=".upload-", dir=archive.directory) as directory:
            path = Path(directory) / ("source" + ext)
            size = 0
            with path.open("xb") as output:
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise AssetLibraryError(413, "MEDIA_TOO_LARGE", "图片最大 10 MiB，视频最大 100 MiB")
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            with archive.prepare(path, kind) as (temporary, details):
                declared = (file.content_type or "").split(";")[0].strip().lower()
                if declared and declared not in {"application/octet-stream", details["mime_type"]}:
                    raise AssetLibraryError(415, "MEDIA_FORMAT_INVALID", "文件声明类型与真实内容不符")
                return library.upload_media(source, kind, payload, archive, temporary, details, asset_id)
    except OSError as exc:
        raise AssetLibraryError(503, "MEDIA_STORE_UNAVAILABLE", "上传归档暂不可用，请保留文件并重试") from exc
