"""Finish a bounded database unit before propagating caller cancellation."""

import asyncio
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


def finish_db_work(function: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    """Protect lock, session, cursor and transaction cleanup as one owned task.

    SQLite statements can outlive a cancelled aiosqlite await even after connection
    invalidation. Do not decorate network operations or callbacks: only bounded DB
    work belongs here. Cancellation still reaches the caller after that work settles.
    """

    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        task = asyncio.create_task(function(*args, **kwargs))
        cancelled = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                if task.cancelled():
                    raise
                cancelled = cancelled or exc
            except Exception as exc:
                if cancelled is not None:
                    raise cancelled from exc
                raise
        if cancelled is not None:
            try:
                task.result()  # Observe errors; never leave a detached failed task.
            except Exception as exc:
                raise cancelled from exc
            raise cancelled
        return task.result()

    return wrapped
