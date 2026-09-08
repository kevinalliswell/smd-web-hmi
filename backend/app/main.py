"""FastAPI 应用入口：注册路由、WebSocket，启动 HostComm 客户端。"""

from __future__ import annotations

import re
import uuid
from contextlib import asynccontextmanager, nullcontext

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app import __version__
from app.api import websocket
from app.api.operation_api import operation_http_error
from app.api.routes import (
    alarms,
    analytics,
    auth,
    commands,
    control,
    experiment_review,
    logs,
    maintenance,
    parameters,
    recipes,
    reports,
    run_recoveries,
    status,
    system,
    tests,
    users,
)
from app.api.schemas import err
from app.api.ws_manager import ws_manager
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.security import hash_password
from app.db.database import assert_schema_current, create_all, dispose_engine, get_sessionmaker
from app.db.models import UserAccount
from app.hostcomm.client import HostCommClient, HostCommNotConnectedError, HostCommProtocolError, HostCommTimeoutError
from app.hostcomm.protocol import now_iso
from app.hostcomm.v2_transport import V2TransportError
from app.services.background_jobs import background_jobs
from app.services.cache import status_cache
from app.services.gateway_lease import GatewayLease
from app.services.maintenance_service import MaintenanceBlockedError, maintenance_manager
from app.services.operations import OperationError, recover_interrupted_operations
from app.services.sampling_health import sampling_health
from app.services.state_policy import enrich_status_snapshot
from app.services.test_runtime import active_test
from app.services.test_session_service import advance_test_session, reconcile_test_sessions
from app.services.v2_operations import V2OperationError
from app.services.v2_recovery_worker import V2RecoveryWorker
from app.services.v2_run_recovery import V2RunRecoveryService

logger = get_logger("main")

SAMPLING_ALARM_CODE = "HMI-DATA-PERSISTENCE"


async def _broadcast_sampling_alarm(*, active: bool, test_id: str | None = None) -> None:
    """数据库不可写时仍通过内存 WebSocket 通道暴露数据完整性风险。"""
    if active:
        await ws_manager.broadcast(
            "alarm_new",
            {
                "alarm_id": SAMPLING_ALARM_CODE,
                "alarm_code": SAMPLING_ALARM_CODE,
                "level": 2,
                "text": "上位机连续写库失败，试验采样数据可能出现缺口",
                "occur_time": now_iso(),
                "latched": False,
                "test_id": test_id,
                "source": "hmi",
            },
        )
        return
    await ws_manager.broadcast(
        "alarm_clear",
        {
            "alarm_id": SAMPLING_ALARM_CODE,
            "alarm_code": SAMPLING_ALARM_CODE,
            "clear_time": now_iso(),
        },
    )


async def _seed_admin() -> None:
    """首次启动创建管理员；生产环境必须由安装器提供一次性口令。"""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(select(UserAccount).where(UserAccount.username == "admin"))
        if result.scalar_one_or_none() is None:
            settings = get_settings()
            password = getattr(settings, "bootstrap_admin_password", "") or getattr(
                settings, "smd_bootstrap_admin_password", ""
            )
            if not password and settings.hostcomm_mock:
                password = "admin"
            if not password:
                raise RuntimeError(
                    "首次生产启动必须配置 SMD_BOOTSTRAP_ADMIN_PASSWORD 或 SMD_BOOTSTRAP_ADMIN_PASSWORD_FILE"
                )
            if not settings.hostcomm_mock and len(password) < 12:
                raise RuntimeError("首次管理员口令至少需要 12 个字符")
            session.add(
                UserAccount(
                    username="admin",
                    hashed_pw=hash_password(password),
                    role="admin",
                    display_name="系统管理员",
                    is_active=1,
                    must_change_password=1,
                    created_at=now_iso(),
                )
            )
            await session.commit()
            logger.warning("seed.admin_created", note="已创建一次性管理员账户，首次登录必须修改密码")


async def _persist_snapshot(payload: dict) -> None:
    """将状态快照写入 device_status 滚动缓冲；试验进行中再写 sample_point。

    写库失败不得影响实时推送，异常仅记录。
    """
    from app.services import logging_service

    test_id = active_test.active_test_id
    payload = dict(payload, _hmi={"persistence_failures": sampling_health.consecutive_write_failures})
    try:
        settings = get_settings()
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            await logging_service.append_device_status(
                session,
                payload,
                retention_hours=settings.smd_device_status_retention_hours,
                cleanup_interval_seconds=settings.smd_device_status_cleanup_interval_seconds,
            )
            # 仅已建档或已完成重启对账的会话允许写采样，避免设备上报的未知
            # test_id 在本地形成没有 test_session 外键语义的孤儿数据。
            test_id = active_test.active_test_id
            if test_id:
                await logging_service.append_sample_point(session, test_id, payload, commit=False)
                await advance_test_session(session, test_id, payload)
    except Exception as exc:  # noqa: BLE001
        should_alarm = sampling_health.record_failure()
        logger.warning(
            "persist.snapshot_failed",
            error=str(exc),
            consecutive_failures=sampling_health.consecutive_write_failures,
            test_id=test_id,
        )
        if should_alarm:
            await _broadcast_sampling_alarm(active=True, test_id=test_id)
    else:
        if sampling_health.record_success():
            await _broadcast_sampling_alarm(active=False)


async def _reconcile_test_runtime(device_snapshot: dict | None = None) -> None:
    """从数据库恢复运行态；首次设备快照到达后完成最终对账。"""
    try:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            result = await reconcile_test_sessions(session, device_snapshot)
        logger.info("test_session.reconciled", **result)
    except Exception as exc:  # noqa: BLE001
        logger.warning("test_session.reconcile_failed", error=str(exc))


def _build_hostcomm_client(settings, *, device_host: str | None = None) -> HostCommClient:
    """根据配置构造 HostComm 客户端并接好回调（缓存 / WebSocket 广播）。"""
    host = "127.0.0.1" if settings.hostcomm_mock else (device_host or settings.hostcomm_host)

    last_device_identity = None

    async def on_status(payload: dict) -> None:
        nonlocal last_device_identity
        payload = enrich_status_snapshot(payload, control_ready=False)
        await status_cache.update(payload, ts_iso=(payload.get("_hostcomm") or {}).get("received_at") or now_iso())
        try:
            maintenance_idle = maintenance_manager.upgrade_state()["state"] == "idle"
        except MaintenanceBlockedError:
            maintenance_idle = False
        payload = enrich_status_snapshot(
            payload, control_ready=client.is_online and status_cache.is_fresh and maintenance_idle
        )
        observed = (payload.get("state_machine") or {}).get("test_id")
        identity_changed = observed != last_device_identity
        if payload.get("_v2_persisted"):
            # Source evidence and run boundaries are already committed by the v2
            # projector. A status read must neither create a sample nor run the
            # legacy arrival-order lifecycle reducer.
            run = (((payload.get("_v2") or {}).get("status") or {}).get("payload") or {}).get("run") or {}
            if observed and not run.get("safe_complete"):
                active_test.restore(observed, needs_device_reconcile=False)
            else:
                active_test.stop()
            last_device_identity = observed
        elif status_cache.is_fresh and (active_test.needs_device_reconcile or identity_changed):
            await _reconcile_test_runtime(payload)
            last_device_identity = observed
        await ws_manager.broadcast("status_update", payload)
        if not payload.get("_v2_persisted"):
            await _persist_snapshot(payload)

    from app.db.cancellation import finish_db_work

    @finish_db_work
    async def alarm_notification(event):
        from app.db.models import AlarmLog
        from app.db.v2_models import V2AlarmProjection

        async with get_sessionmaker()() as session:
            binding = await session.get(
                V2AlarmProjection, (client.device_id, event["alarm_id"], event["occurrence_seq"])
            )
            alarm = await session.get(AlarmLog, binding.alarm_log_id) if binding else None
            if alarm is None:
                return None
            # Materialize committed projection before closing; broadcasting is outside DB work.
            event_type = "alarm_clear" if not binding.active else "alarm_ack" if binding.acknowledged else "alarm_new"
            return event_type, {
                "alarm_id": alarm.id,
                "alarm_code": alarm.alarm_code,
                "level": alarm.level,
                "text": alarm.text,
                "occur_time": alarm.occur_time,
                "clear_time": alarm.clear_time,
                "ack_time": alarm.ack_time,
                "ack_operator": alarm.ack_operator,
                "wire_alarm_id": event["alarm_id"],
                "occurrence_seq": event["occurrence_seq"],
                "device_id": client.device_id,
            }

    async def on_event(payload: dict) -> None:
        if payload.get("_v2_persisted"):
            if payload.get("kind") == "alarm":
                notification = await alarm_notification(payload["payload"])
                if notification:
                    await ws_manager.broadcast(*notification)
                    return
            await ws_manager.broadcast("event", payload)
            return
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
            # 数据库故障时仍保持报警类型，让操作员看到未持久化的现场报警。
            if payload.get("kind") in {"alarm_new", "alarm_clear"}:
                ws_type = payload["kind"]
                ws_data = {**payload, "alarm_code": payload.get("event_code"), "persisted": False}
        await ws_manager.broadcast(ws_type, ws_data)

    async def on_comm_status(payload: dict) -> None:
        if payload.get("status") in {"offline", "degraded"}:
            if hasattr(status_cache, "invalidate"):
                status_cache.invalidate()
            if active_test.active_test_id:
                active_test.restore(active_test.active_test_id, needs_device_reconcile=True)
        await ws_manager.broadcast("comm_status", payload)

    if getattr(settings, "protocol_version", "1.0") == "2.0":
        from app.hostcomm.v2_client import V2Client

        client = V2Client(
            host,
            settings.hostcomm_port,
            factory=get_sessionmaker(),
            device_id=settings.hostcomm_device_id,
            controller_id=settings.hostcomm_controller_id,
            controller_epoch=settings.hostcomm_controller_epoch,
            psk_file=settings.hostcomm_psk_file,
            mock=settings.hostcomm_mock,
            client_version=__version__,
            on_status=on_status,
            on_event=on_event,
            on_comm_status=on_comm_status,
        )
        return client

    client = HostCommClient(
        host=host,
        port=settings.hostcomm_port,
        heartbeat_interval=settings.hostcomm_heartbeat_interval,
        timeout_count=settings.hostcomm_timeout_count,
        command_timeout=settings.hostcomm_command_timeout,
        client_id=settings.client_id,
        client_version=__version__,
        on_status=on_status,
        on_event=on_event,
        on_comm_status=on_comm_status,
    )
    return client


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：建表、播种、启动 HostComm。"""
    settings = get_settings()
    configure_logging(production=not settings.hostcomm_mock)
    settings.validate_startup(logger)
    logger.info("app.starting", version=__version__, mock=settings.hostcomm_mock)

    lease = None if settings.hostcomm_mock else GatewayLease(settings.hostcomm_host, settings.hostcomm_port)
    with nullcontext() if lease is None else lease:
        client = None
        recovery_worker = None
        try:
            # 开发/联调允许按 ORM 元数据建表；生产必须由安装/升级流程执行受控迁移。
            if settings.hostcomm_mock:
                await create_all()
            else:
                await assert_schema_current()
            maintenance_manager.configure_upgrade(settings.maintenance_file)
            await _seed_admin()
            async with get_sessionmaker()() as session:
                await recover_interrupted_operations(session)
            await _reconcile_test_runtime()
            if not settings.hostcomm_mock:
                await maintenance_manager.start(settings)

            client = _build_hostcomm_client(settings, device_host=None if settings.hostcomm_mock else lease.host)
            app.state.hostcomm_client = client
            recovery_worker = V2RecoveryWorker(
                V2RunRecoveryService(get_sessionmaker(), write_lock=getattr(client, "_write_lock", None))
            )
            app.state.run_recovery_worker = recovery_worker
            await recovery_worker.start()
            await client.start()  # 失败不阻断启动，转后台重连
            if client.is_online and active_test.needs_device_reconcile:
                try:
                    await client.get_status()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("test_session.initial_status_failed", error=str(exc))
            yield
        finally:
            try:
                if client is not None:
                    await client.close()
            finally:
                if recovery_worker is not None:
                    await recovery_worker.close()
                await maintenance_manager.stop()
                await background_jobs.shutdown()
                await dispose_engine()
                logger.info("app.stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    docs_enabled = settings.hostcomm_mock
    app = FastAPI(
        title="smd-web-hmi 后端",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )

    request_id_pattern = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")

    @app.middleware("http")
    async def _security_boundary(request: Request, call_next):
        supplied_request_id = request.headers.get("X-Request-ID", "")
        request_id = supplied_request_id if request_id_pattern.fullmatch(supplied_request_id) else uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        response.headers["X-Request-ID"] = request_id
        response.headers["X-SMD-Version"] = __version__
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; object-src 'none'; "
            "img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "connect-src 'self' ws: wss:"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    # 生产同源部署默认不开放 CORS；跨域调试须显式列出来源。
    # Mock 开发模式允许通配，但 JWT 不使用 Cookie，始终禁用跨域凭证。
    cors_origins = settings.cors_origins
    if cors_origins:
        wildcard = cors_origins == ["*"]
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=False,
            allow_methods=["*"] if wildcard else ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["*"] if wildcard else ["Authorization", "Content-Type"],
        )

    # REST 路由
    for module in (
        auth,
        status,
        commands,
        control,
        experiment_review,
        tests,
        alarms,
        parameters,
        logs,
        reports,
        run_recoveries,
        recipes,
        analytics,
        users,
        system,
        maintenance,
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
        safe_errors = [
            {
                "location": ".".join(str(part) for part in item.get("loc", ())),
                "message": item.get("msg", "输入无效"),
                "type": item.get("type", "validation_error"),
            }
            for item in exc.errors()
        ]
        return JSONResponse(status_code=422, content=err("validation_error", str(safe_errors)))

    @app.exception_handler(HostCommTimeoutError)
    async def _hostcomm_timeout_handler(request: Request, exc: HostCommTimeoutError):
        return JSONResponse(status_code=504, content=err("device_comm_timeout", str(exc)))

    @app.exception_handler(HostCommNotConnectedError)
    async def _hostcomm_offline_handler(request: Request, exc: HostCommNotConnectedError):
        return JSONResponse(status_code=503, content=err("device_comm_fault", str(exc)))

    async def _operation_error_handler(request: Request, exc: Exception):
        error = operation_http_error(exc)
        return JSONResponse(status_code=error.status_code, content=error.detail)

    for error_class in (
        OperationError,
        MaintenanceBlockedError,
        HostCommProtocolError,
        V2TransportError,
        V2OperationError,
    ):
        app.add_exception_handler(error_class, _operation_error_handler)

    @app.get("/health", tags=["system"])
    async def health():  # noqa: D401
        """根级健康检查（便于探针）。"""
        return {"status": "ok", "version": __version__}

    # 生产形态：托管前端构建产物（vite build 输出），同源伺服免 CORS。
    # 未配置且默认位置无产物时不注册任何路由（开发模式走 Vite dev server）。
    # 注意：SPA 回退是 catch-all 路由，必须在所有 API 路由之后注册。
    dist_dir = settings.frontend_dist_dir
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
