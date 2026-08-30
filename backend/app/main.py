"""FastAPI 应用入口：注册路由、WebSocket，启动 HostComm 客户端。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app import __version__
from app.api import websocket
from app.api.routes import alarms, analytics, auth, commands, logs, parameters, reports, status, system, tests, users
from app.api.schemas import err
from app.api.ws_manager import ws_manager
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.security import hash_password
from app.db.database import create_all, dispose_engine, get_sessionmaker
from app.db.models import UserAccount
from app.hostcomm.client import HostCommClient
from app.hostcomm.protocol import now_iso
from app.services.cache import status_cache
from app.services.state_policy import enrich_status_snapshot

logger = get_logger("main")


async def _seed_admin() -> None:
    """首次启动插入默认 admin/admin 账户（提示修改密码）。"""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(select(UserAccount).where(UserAccount.username == "admin"))
        if result.scalar_one_or_none() is None:
            session.add(
                UserAccount(
                    username="admin",
                    hashed_pw=hash_password("admin"),
                    role="admin",
                    display_name="系统管理员",
                    is_active=1,
                    created_at=now_iso(),
                )
            )
            await session.commit()
            logger.warning("seed.admin_created", note="默认密码 admin/admin，请尽快修改")


async def _persist_snapshot(payload: dict) -> None:
    """将状态快照写入 device_status 滚动缓冲；试验进行中再写 sample_point。

    写库失败不得影响实时推送，异常仅记录。
    """
    from app.services import logging_service
    from app.services.test_runtime import active_test

    try:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            await logging_service.append_device_status(session, payload)
            test_id = active_test.active_test_id or (payload.get("state_machine", {}) or {}).get("test_id")
            if test_id:
                await logging_service.append_sample_point(session, test_id, payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("persist.snapshot_failed", error=str(exc))


def _build_hostcomm_client(settings) -> HostCommClient:
    """根据配置构造 HostComm 客户端并接好回调（缓存 / WebSocket 广播）。"""
    host = "127.0.0.1" if settings.hostcomm_mock else settings.hostcomm_host

    async def on_status(payload: dict) -> None:
        payload = enrich_status_snapshot(payload)
        await status_cache.update(payload, ts_iso=now_iso())
        await ws_manager.broadcast("status_update", payload)
        await _persist_snapshot(payload)

    async def on_event(payload: dict) -> None:
        # 事件落库（event_log / alarm_log，只追加）并按结果广播
        ws_type, ws_data = "event", payload
        try:
            sessionmaker = get_sessionmaker()
            async with sessionmaker() as session:
                from app.services import alarm_service

                result = await alarm_service.handle_event(session, payload)
                if result is not None:
                    ws_type, ws_data = result
        except Exception as exc:  # noqa: BLE001
            logger.warning("persist.event_failed", error=str(exc))
        await ws_manager.broadcast(ws_type, ws_data)

    async def on_comm_status(payload: dict) -> None:
        await ws_manager.broadcast("comm_status", payload)

    return HostCommClient(
        host=host,
        port=settings.hostcomm_port,
        heartbeat_interval=settings.hostcomm_heartbeat_interval,
        timeout_count=settings.hostcomm_timeout_count,
        command_timeout=settings.hostcomm_command_timeout,
        client_id=settings.client_id,
        on_status=on_status,
        on_event=on_event,
        on_comm_status=on_comm_status,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：建表、播种、启动 HostComm。"""
    settings = get_settings()
    configure_logging()
    logger.info("app.starting", version=__version__, mock=settings.hostcomm_mock)

    # 开发/联调：按 ORM 元数据建表（生产用 alembic upgrade head）
    await create_all()
    await _seed_admin()

    client = _build_hostcomm_client(settings)
    app.state.hostcomm_client = client
    await client.start()  # 失败不阻断启动，转后台重连

    try:
        yield
    finally:
        await client.close()
        await dispose_engine()
        logger.info("app.stopped")


def create_app() -> FastAPI:
    app = FastAPI(title="smd-web-hmi 后端", version=__version__, lifespan=lifespan)

    # 本地工控机：允许同网段浏览器访问
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # REST 路由
    for module in (
        auth,
        status,
        commands,
        tests,
        alarms,
        parameters,
        logs,
        reports,
        analytics,
        users,
        system,
    ):
        app.include_router(module.router)

    # WebSocket
    app.include_router(websocket.router)

    # 统一错误响应为顶层 {error_code, message, ts}（规格 §3），而非 FastAPI 默认的 {detail:...}
    @app.exception_handler(HTTPException)
    async def _http_exc_handler(request: Request, exc: HTTPException):
        detail = exc.detail
        if isinstance(detail, dict) and "error_code" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail, headers=exc.headers)
        return JSONResponse(
            status_code=exc.status_code,
            content=err("http_error", str(detail)),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exc_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content=err("validation_error", str(exc.errors())))

    @app.get("/health", tags=["system"])
    async def health():  # noqa: D401
        """根级健康检查（便于探针）。"""
        return {"status": "ok", "version": __version__}

    # 生产形态：托管前端构建产物（vite build 输出），同源伺服免 CORS。
    # 未配置且默认位置无产物时不注册任何路由（开发模式走 Vite dev server）。
    # 注意：SPA 回退是 catch-all 路由，必须在所有 API 路由之后注册。
    dist_dir = get_settings().frontend_dist_dir
    if dist_dir is not None:
        if (dist_dir / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=dist_dir / "assets"), name="assets")
        index_file = dist_dir / "index.html"
        dist_root = dist_dir.resolve()

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):
            """SPA 路由回退：/api、/ws 之外的未知路径一律返回 index.html。"""
            if full_path in ("api", "ws") or full_path.startswith(("api/", "ws/")):
                raise HTTPException(status_code=404, detail=err("not_found", "接口不存在"))
            target = (dist_dir / full_path).resolve()
            if full_path and target.is_file() and target.is_relative_to(dist_root):
                return FileResponse(target)
            return FileResponse(index_file)

    return app


app = create_app()
