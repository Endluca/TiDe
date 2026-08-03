from __future__ import annotations

import os
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope


FRONTEND_STATIC_ROOT = Path(__file__).resolve().parent / "static"


class _FrontendAssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: set[str] = set()

    def handle_starttag(
        self,
        _tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        for name, value in attrs:
            if name in {"href", "src"} and value and value.startswith("/assets/"):
                self.references.add(value)


class ImmutableAssetFiles(StaticFiles):
    """Serve Vite's content-hashed assets with an immutable cache policy."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code < 400:
            response.headers["Cache-Control"] = (
                "public, max-age=31536000, immutable"
            )
        return response


def _frontend_required() -> bool:
    return os.getenv("TIT_FRONTEND_REQUIRED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _index_response(index_path: Path) -> FileResponse:
    return FileResponse(
        index_path,
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


def _frontend_build_error(static_root: Path) -> str | None:
    index_path = static_root / "index.html"
    assets_path = static_root / "assets"
    if not index_path.is_file() or not assets_path.is_dir():
        return f"expected {index_path} and {assets_path}"

    parser = _FrontendAssetParser()
    try:
        parser.feed(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        return f"cannot read {index_path}: {exc}"
    if not parser.references:
        return f"{index_path} does not reference any /assets/ build output"

    resolved_assets = assets_path.resolve()
    missing: list[str] = []
    for reference in sorted(parser.references):
        relative_path = unquote(urlsplit(reference).path).removeprefix("/")
        candidate = (static_root / relative_path).resolve()
        if not candidate.is_relative_to(resolved_assets) or not candidate.is_file():
            missing.append(reference)
    if missing:
        return "missing index asset references: " + ", ".join(missing)
    return None


def install_frontend_static(
    application: FastAPI,
    *,
    static_root: Path = FRONTEND_STATIC_ROOT,
    required: bool | None = None,
) -> bool:
    """Install the packaged operations UI after every API route.

    Source checkouts intentionally remain runnable without a frontend build so
    Vite can provide HMR during development. The Gaea image sets
    ``TIT_FRONTEND_REQUIRED=true`` and copies the build into this package, so a
    missing or partial artifact fails closed there.
    """

    require_build = _frontend_required() if required is None else required
    build_error = _frontend_build_error(static_root)
    if build_error:
        if require_build:
            raise RuntimeError(
                "Packaged frontend build is incomplete: " + build_error
            )
        return False

    index_path = static_root / "index.html"
    assets_path = static_root / "assets"

    application.mount(
        "/assets",
        ImmutableAssetFiles(directory=assets_path),
        name="frontend-assets",
    )

    @application.get("/", include_in_schema=False)
    def frontend_index() -> FileResponse:
        return _index_response(index_path)

    return True
