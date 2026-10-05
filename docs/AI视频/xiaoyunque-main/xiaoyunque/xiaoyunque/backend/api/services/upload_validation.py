"""Validate the legacy upload endpoints without trusting extensions or MIME alone."""

import json
import math
import subprocess
import zipfile
from pathlib import Path
from urllib.parse import unquote

from fastapi import HTTPException

from core.media_archive import MediaArchive
from models.pdf_reader import PDFReadError


def upload_filename(value: str | None) -> str:
    raw = unquote(value or "")
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    if not name or len(name) > 180 or any(ord(c) < 32 or ord(c) == 127 for c in raw):
        raise HTTPException(422)
    return name


def declared_type(file, expected: str | set[str]) -> None:
    allowed = {expected} if isinstance(expected, str) else expected
    declared = (file.content_type or "").split(";")[0].lower().strip()
    if declared and declared not in allowed | {"application/octet-stream"}:
        raise HTTPException(415)


def validate_document(path: Path, file) -> None:
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            with path.open("rb") as stream:
                if not stream.read(5) == b"%PDF-":
                    raise ValueError("Not PDF")
            from models.pdf_reader import read_pdf
            read_pdf(str(path), extract=False)
            declared_type(file, "application/pdf")
        elif ext in {".docx", ".doc"}:
            if ext == ".doc" and path.read_bytes()[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
                from models.file_reader import FileReader
                try:
                    FileReader._extract_legacy_doc(str(path))
                except (RuntimeError, subprocess.TimeoutExpired) as exc:
                    raise HTTPException(503) from exc
                declared_type(file, "application/msword")
                return
            with zipfile.ZipFile(path) as document:
                if sum(item.file_size for item in document.infolist()) > 100 * 1024 * 1024:
                    raise HTTPException(413)
                if "word/document.xml" not in document.namelist():
                    raise ValueError("Not Word")
                from docx import Document
                Document(path)
            declared_type(file, {"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/msword"} if ext == ".doc" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        else:
            content = path.read_bytes()
            text = None
            encodings = ["utf-16"] if content.startswith((b"\xff\xfe", b"\xfe\xff")) else ["utf-8-sig", "gbk"]
            for encoding in encodings:
                try:
                    text = content.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            if text is None or any(ord(c) < 32 and c not in "\t\n\r" for c in text):
                raise ValueError("Not text")
            declared_type(file, {"text/plain", "text/markdown"})
    except HTTPException:
        raise
    except PDFReadError as exc:
        raise HTTPException(503 if exc.unavailable else 415) from exc
    except Exception as exc:
        raise HTTPException(415) from exc


def validate_media(path: Path, file) -> None:
    ext = path.suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
        details = MediaArchive._image(path)
        declared_type(file, details["mime_type"])
        return
    containers = {".mp4": ({"mov", "mp4"}, "video/mp4"), ".mov": ({"mov", "mp4"}, "video/quicktime"),
                  ".avi": ({"avi"}, "video/x-msvideo"), ".mkv": ({"matroska", "webm"}, "video/x-matroska"),
                  ".webm": ({"matroska", "webm"}, "video/webm")}
    expected, mime = containers[ext]
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
            "-show_entries", "stream=codec_type,width,height:format=format_name,duration", "-of", "json", str(path)],
            capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(503) from exc
    try:
        data = json.loads(result.stdout)
        video = next(s for s in data["streams"] if s.get("codec_type") == "video")
        duration = float(data["format"]["duration"])
        if (result.returncode != 0 or not expected.intersection(data["format"]["format_name"].split(","))
                or not math.isfinite(duration) or duration <= 0
                or type(video["width"]) is not int or type(video["height"]) is not int
                or min(video["width"], video["height"]) <= 0):
            raise ValueError("Invalid video")
    except (ValueError, TypeError, KeyError, StopIteration) as exc:
        raise HTTPException(415) from exc
    declared_type(file, mime)
