from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.frontend_static import install_frontend_static


def _built_frontend(tmp_path: Path) -> Path:
    static_root = tmp_path / "static"
    assets = static_root / "assets"
    assets.mkdir(parents=True)
    (static_root / "index.html").write_text(
        '<!doctype html><div id="root">TiDe</div>'
        '<script type="module" src="/assets/app-abc123.js"></script>',
        encoding="utf-8",
    )
    (assets / "app-abc123.js").write_text(
        "console.log('tide')",
        encoding="utf-8",
    )
    return static_root


def _application(static_root: Path) -> FastAPI:
    application = FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @application.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    assert install_frontend_static(
        application,
        static_root=static_root,
        required=True,
    )
    return application


def test_packaged_frontend_serves_index_and_immutable_assets(
    tmp_path: Path,
) -> None:
    client = TestClient(_application(_built_frontend(tmp_path)))

    index = client.get("/")
    assert index.status_code == 200
    assert index.headers["content-type"].startswith("text/html")
    assert index.headers["cache-control"] == "no-store"
    assert "TiDe" in index.text

    asset = client.get("/assets/app-abc123.js")
    assert asset.status_code == 200
    assert "javascript" in asset.headers["content-type"]
    assert asset.headers["cache-control"] == (
        "public, max-age=31536000, immutable"
    )


def test_static_mount_does_not_shadow_api_or_disabled_docs(
    tmp_path: Path,
) -> None:
    client = TestClient(_application(_built_frontend(tmp_path)))

    for path in (
        "/api/missing",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/workspace/tasks",
        "/missing.js",
        "/assets/missing.js",
    ):
        response = client.get(path, headers={"Accept": "text/html"})
        assert response.status_code == 404, path
        assert "TiDe" not in response.text, path

    post_api = client.post(
        "/api/missing",
        headers={"Accept": "text/html"},
    )
    assert post_api.status_code == 404
    assert "TiDe" not in post_api.text


def test_missing_frontend_build_is_optional_for_source_development(
    tmp_path: Path,
) -> None:
    application = FastAPI()

    assert not install_frontend_static(
        application,
        static_root=tmp_path / "missing",
        required=False,
    )
    assert TestClient(application).get("/").status_code == 404


def test_required_frontend_build_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Packaged frontend build is incomplete"):
        install_frontend_static(
            FastAPI(),
            static_root=tmp_path / "missing",
            required=True,
        )


def test_required_frontend_build_rejects_missing_index_assets(
    tmp_path: Path,
) -> None:
    static_root = _built_frontend(tmp_path)
    (static_root / "index.html").write_text(
        '<script type="module" src="/assets/missing.js"></script>',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="/assets/missing.js"):
        install_frontend_static(
            FastAPI(),
            static_root=static_root,
            required=True,
        )
