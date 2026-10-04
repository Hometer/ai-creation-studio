"""Bounded copies and real format validation for existing project artifacts."""

import hashlib
import json
import math
import os
import shutil
import stat
import subprocess
import tempfile
import warnings
from contextlib import contextmanager
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from api.schemas.assets import MediaInfo
from core.asset_library import AssetLibraryError

IMAGE_LIMIT = 10 * 1024 * 1024
VIDEO_LIMIT = 100 * 1024 * 1024
EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
              "image/bmp": ".bmp", "video/mp4": ".mp4"}
IMAGE_FORMATS = {"JPEG": ({".jpg", ".jpeg"}, "image/jpeg"),
                 "PNG": ({".png"}, "image/png"), "WEBP": ({".webp"}, "image/webp"),
                 "BMP": ({".bmp"}, "image/bmp")}


def media_error(status, code, message):
    return AssetLibraryError(status, code, message)


def sync_directory(directory):
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class MediaArchive:
    def __init__(self, library_directory, result_directory, backend_directory):
        self.directory = Path(library_directory) / "media"
        self.result_directory = Path(result_directory).resolve()
        self.backend_directory = Path(backend_directory).resolve()

    def _directory(self):
        if self.directory.is_symlink() or self.directory.parent.is_symlink():
            raise media_error(503, "MEDIA_STORE_UNAVAILABLE", "媒体归档目录不可用")
        self.directory.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.directory.mkdir(mode=0o700, exist_ok=True)

    def resolve_source(self, value, project_id, kind):
        if not isinstance(value, str) or not value.strip():
            raise media_error(409, "MEDIA_NOT_SELECTED", "当前条目没有可归档的素材选择")
        raw = value.strip()
        if "://" in raw or raw.startswith("//"):
            raise media_error(415, "REMOTE_MEDIA_UNSUPPORTED", "请先使用项目中的本地素材")
        candidate = Path(raw)
        if raw.startswith("/code/"):
            candidate = self.backend_directory / raw.lstrip("/")
        elif not candidate.is_absolute():
            candidate = self.backend_directory / candidate
        root = self.result_directory / ("video" if kind == "video" else "image")
        project_root = root / project_id
        try:
            if root.is_symlink() or project_root.is_symlink():
                raise ValueError("Unsafe project directory")
            resolved = candidate.resolve(strict=True)
            allowed = project_root.resolve(strict=True)
            if not allowed.is_relative_to(self.result_directory) or not resolved.is_relative_to(allowed):
                raise ValueError("Outside project result directory")
            if not resolved.is_file():
                raise ValueError("Not a regular file")
            return resolved
        except (OSError, ValueError, RuntimeError) as exc:
            raise media_error(404, "SOURCE_MEDIA_NOT_FOUND", "素材不存在或不属于该项目的结果目录") from exc

    @contextmanager
    def prepare(self, source, kind):
        temporary = None
        try:
            self._directory()
            ext = source.suffix.lower()
            supported = {".mp4"} if kind == "video" else {e for extensions, _ in IMAGE_FORMATS.values() for e in extensions}
            if ext not in supported:
                raise media_error(415, "MEDIA_FORMAT_INVALID", "素材格式不支持或与内容不符")
            limit = VIDEO_LIMIT if kind == "video" else IMAGE_LIMIT
            descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as input_file:
                info = os.fstat(input_file.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise media_error(415, "MEDIA_FORMAT_INVALID", "素材必须是普通媒体文件")
                if info.st_size > limit:
                    raise media_error(413, "MEDIA_TOO_LARGE", "素材大小超过本地归档上限")
                fd, name = tempfile.mkstemp(prefix=".staging-", suffix=ext, dir=self.directory)
                temporary = Path(name)
                digest, size = hashlib.sha256(), 0
                with os.fdopen(fd, "wb") as output:
                    while chunk := input_file.read(1024 * 1024):
                        size += len(chunk)
                        if size > limit:
                            raise media_error(413, "MEDIA_TOO_LARGE", "素材大小超过本地归档上限")
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            details = self._video(temporary) if kind == "video" else self._image(temporary)
            yield temporary, {**details, "sha256": digest.hexdigest(), "byte_size": size}
        except OSError as exc:
            raise media_error(503, "MEDIA_STORE_UNAVAILABLE", "素材复制或归档暂不可用") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _image(path):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(path) as image:
                    allowed, mime = IMAGE_FORMATS.get(image.format, (set(), None))
                    if path.suffix.lower() not in allowed:
                        raise ValueError("Format mismatch")
                    width, height = image.size
                    if width * height > 40_000_000:
                        raise media_error(413, "IMAGE_DIMENSIONS_TOO_LARGE", "图片像素超过本地归档上限")
                    image.verify()
                with Image.open(path) as image:
                    image.load()
            return {"mime_type": mime, "width": width, "height": height, "duration": None}
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise media_error(413, "IMAGE_DIMENSIONS_TOO_LARGE", "图片像素超过本地归档上限") from exc
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
            raise media_error(415, "MEDIA_FORMAT_INVALID", "图片内容无效或与格式不符") from exc

    @staticmethod
    def _video(path):
        with path.open("rb") as stream:
            header = stream.read(16)
        if len(header) < 16 or header[4:8] != b"ftyp" or header[8:12] == b"qt  ":
            raise media_error(415, "MEDIA_FORMAT_INVALID", "视频内容必须为有效 MP4")
        executable = shutil.which("ffprobe")
        if executable is None:
            raise media_error(503, "MEDIA_PROBE_UNAVAILABLE", "视频校验工具不可用")
        try:
            result = subprocess.run([
                executable, "-v", "error", "-protocol_whitelist", "file", "-show_entries",
                "format=format_name,duration:stream=codec_type,width,height", "-of", "json", str(path),
            ], capture_output=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise media_error(503, "MEDIA_PROBE_UNAVAILABLE", "视频校验暂不可用，请稍后重试") from exc
        try:
            document = json.loads(result.stdout)
            video = next(s for s in document["streams"] if s.get("codec_type") == "video")
            width, height = video["width"], video["height"]
            duration = float(document["format"]["duration"])
            if (result.returncode != 0 or "mp4" not in document["format"]["format_name"].split(",")
                    or type(width) is not int or type(height) is not int or width <= 0 or height <= 0
                    or not math.isfinite(duration) or duration <= 0):
                raise ValueError("Invalid video stream")
        except (KeyError, TypeError, ValueError, StopIteration) as exc:
            raise media_error(415, "MEDIA_FORMAT_INVALID", "视频内容或视频流无效") from exc
        return {"mime_type": "video/mp4", "width": width, "height": height, "duration": duration}

    def path(self, media: MediaInfo):
        return self.directory / (media.file_id + EXTENSIONS[media.mime_type])

    def publish(self, temporary, media):
        # Hard-link publication is exclusive: an existing archive is never overwritten.
        destination = self.path(media)
        os.link(temporary, destination, follow_symlinks=False)
        try:
            sync_directory(self.directory)
        except OSError:
            destination.unlink(missing_ok=True)
            raise
        return destination

    def verify_file(self, media):
        try:
            self._directory()
            path = self.path(media)
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size != media.byte_size:
                    raise ValueError("Media size mismatch")
                digest = hashlib.sha256()
                remaining = media.byte_size
                while remaining:
                    chunk = stream.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        raise ValueError("Incomplete media")
                    digest.update(chunk)
                    remaining -= len(chunk)
                if stream.read(1) or digest.hexdigest() != media.sha256:
                    raise ValueError("Media digest mismatch")
            return path
        except (OSError, ValueError) as exc:
            raise media_error(503, "ARCHIVED_MEDIA_UNAVAILABLE", "归档文件缺失或完整性异常，请保留数据并检查备份") from exc
