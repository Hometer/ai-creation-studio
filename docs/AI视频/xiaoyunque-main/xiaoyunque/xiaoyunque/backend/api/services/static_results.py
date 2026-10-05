"""Preserve legacy result URLs without publishing business stores or source files."""

from pathlib import Path, PurePosixPath

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles


class ResultStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        parts = PurePosixPath(path).parts
        if (not parts or parts[0] != "result" or ".." in parts
                or any(part.startswith(".") for part in parts)
                or tuple(part.casefold() for part in parts) == ("result", "sandbox", "history.json")):
            raise HTTPException(status_code=404)
        root = Path(self.directory) / "result"
        if root.is_symlink() or not (Path(self.directory) / path).resolve().is_relative_to(root.resolve()):
            raise HTTPException(status_code=404)
        return await super().get_response(path, scope)
