"""Run untrusted PDF parsing in a bounded child process; never echo its stderr."""

import json
import subprocess
import sys
from pathlib import Path


class PDFReadError(ValueError):
    def __init__(self, message, unavailable=False):
        super().__init__(message)
        self.unavailable = unavailable


def read_pdf(path: str, extract: bool = True) -> str:
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()),
            "extract" if extract else "validate", path], capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PDFReadError("PDF parser unavailable or timed out", unavailable=True) from exc
    try:
        data = json.loads(result.stdout)
        if result.returncode != 0 or not isinstance(data, dict) or not isinstance(data.get("text"), str):
            raise ValueError("Invalid result")
        return data["text"]
    except (ValueError, TypeError) as exc:
        raise PDFReadError("PDF document unreadable") from exc


def _main() -> int:
    try:
        from pypdf import PdfReader
        reader = PdfReader(sys.argv[2], strict=True)
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 1000:
            return 1
        chunks = []
        size = 0
        if sys.argv[1] == "extract":
            for page in reader.pages:
                text = page.extract_text() or ""
                size += len(text.encode("utf-8"))
                if size > 20 * 1024 * 1024:
                    return 1
                chunks.append(text)
        print(json.dumps({"text": "\n".join(chunks).strip()}))
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(_main())
