"""命令和参数REST入口共享事务身份及错误响应。"""

from fastapi import HTTPException

from app.api.schemas import err
from app.hostcomm.client import HostCommNotConnectedError, HostCommProtocolError, HostCommTimeoutError
from app.hostcomm.v2_transport import V2CapacityError, V2OfflineError, V2RequestTimeout, V2TransportError
from app.services.command_service import CommandError
from app.services.maintenance_service import MaintenanceBlockedError
from app.services.operations import OperationError
from app.services.v2_operations import V2OperationError

OPERATION_ERRORS = (
    CommandError,
    OperationError,
    HostCommTimeoutError,
    HostCommNotConnectedError,
    HostCommProtocolError,
    MaintenanceBlockedError,
    V2TransportError,
    V2OperationError,
)


def request_operation_id(body_id: str | None, header_id: str | None) -> str | None:
    if body_id and header_id and body_id != header_id:
        raise OperationError(409, "operation_conflict", "请求体与Idempotency-Key不一致")
    return body_id or header_id


def operation_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (CommandError, OperationError)):
        status, code, message = exc.status_code, exc.error_code, exc.message
    elif isinstance(exc, V2OperationError):
        status, code, message = 409, exc.code, str(exc)
    elif isinstance(exc, (HostCommTimeoutError, V2RequestTimeout)):
        status, code, message = 504, "device_comm_timeout", "设备响应超时，执行结果需查证"
    elif isinstance(exc, (HostCommNotConnectedError, V2OfflineError)):
        status, code, message = 503, "device_comm_fault", str(exc)
    elif isinstance(exc, MaintenanceBlockedError):
        status, code, message = 503, "maintenance_active", str(exc)
    elif isinstance(exc, V2CapacityError):
        status, code, message = 503, "device_read_capacity", "设备请求繁忙，请稍后查询"
    else:
        status, code, message = 502, "device_protocol_error", str(exc)
    detail = err(code, message)
    if getattr(exc, "operation_id", None):
        detail["operation_id"] = exc.operation_id
    return HTTPException(status_code=status, detail=detail)
