"""Workstation-only previews and release building. Upload is a separate local command."""
from pathlib import Path
import json
from urllib.parse import urlparse
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from database import engine
from services.publishing import build_bundle, ROOT
from services.site import public_mode


class PublishInput(BaseModel):
    ranking_ids: list[int] = Field(min_length=1, max_length=100)
    all_episodes: bool = False


class TransferInput(BaseModel):
    activate: bool = False
    rights_confirmed: bool = False


def router_for(local_write):
    router = APIRouter()

    def local(request: Request):
        local_write(request)
        if public_mode() or request.client.host not in ("127.0.0.1", "::1", "testclient") or urlparse(str(request.url)).hostname not in ("127.0.0.1", "localhost", "testserver", "::1"):
            raise HTTPException(403, "发布操作只在本机工作台提供")

    def build(data, preview):
        return build_bundle(Path(engine.url.database), ROOT / "data", ROOT / "data/publish", data.ranking_ids,
                            preview=preview, representatives_only=not data.all_episodes)

    @router.post("/api/publishing/preview", dependencies=[Depends(local)])
    def preview(data: PublishInput):
        return build(data, True)

    @router.post("/api/publishing/build", dependencies=[Depends(local)])
    def package(data: PublishInput):
        return build(data, False)

    @router.get("/api/publishing/config")
    def config(request: Request):
        import auth
        from services.publish_transfer import configuration
        auth.require_admin(request)
        if request.client.host not in ("127.0.0.1", "::1", "testclient"):
            raise HTTPException(403, "请在本机查看发布配置")
        value = configuration()
        return {"configured": value is not None, "domain": value["domain"] if value else None,
                "inventory_confirmed": value.get("inventory_confirmed") is True if value else False}

    @router.post("/api/publishing/bundles/{release_id}/transfer", dependencies=[Depends(local)])
    def transfer(release_id: str, data: TransferInput):
        from services.publish_transfer import start_transfer
        return start_transfer(release_id, activate=data.activate, rights_confirmed=data.rights_confirmed)

    @router.get("/api/publishing/operations/{identifier}")
    def operation(identifier: str, request: Request):
        import auth
        from services.publish_transfer import transfer_status
        auth.require_admin(request)
        if request.client.host not in ("127.0.0.1", "::1", "testclient"):
            raise HTTPException(403, "请在本机查看发布记录")
        return transfer_status(identifier)

    @router.get("/api/publishing/history")
    def history(request: Request):
        # Read access still requires login and loopback; do not disclose local paths.
        import auth
        auth.require_admin(request)
        if request.client.host not in ("127.0.0.1", "::1", "testclient"):
            raise HTTPException(403, "请在本机查看发布记录")
        directory = ROOT / "data/publish"
        records = []
        for path in sorted(directory.glob("*/manifest.json"), reverse=True):
            value = json.loads(path.read_text(encoding="utf-8"))
            records.append({key: value[key] for key in ("release_id", "created_at", "video_count", "gaps", "ranking_ids")})
        return {"bundles": records[:30], "published": json.loads((directory / "last-published.json").read_text(encoding="utf-8")) if (directory / "last-published.json").exists() else None}

    return router
