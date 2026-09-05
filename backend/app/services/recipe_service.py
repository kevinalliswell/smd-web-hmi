"""配方保存、设备校验和激活；所有设备变更复用参数事务。"""

from __future__ import annotations

import json
import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.recipe_models import RecipeVersion
from app.hostcomm.protocol import now_iso
from app.services.command_service import CommandError, audit_action, check_permission
from app.services.operations import canonical_json
from app.services.recipe_definition import RecipeDefinition, validate_for_device


def definition_model(definition: RecipeDefinition | dict) -> RecipeDefinition:
    try:
        return RecipeDefinition.model_validate(definition)
    except ValidationError as exc:
        raise CommandError(422, "invalid_recipe", str(exc)) from exc


def version_payload(row: RecipeVersion) -> dict[str, Any]:
    return {
        "recipe_id": row.recipe_id,
        "version": row.version,
        "digest": row.digest,
        "definition": json.loads(row.definition_json),
        "created_at": row.created_at,
        "created_by": row.created_by,
    }


def bundle_payload(recipe: dict) -> dict:
    return {key: recipe[key] for key in ("recipe_id", "version", "digest", "definition")}


class RecipeService:
    def __init__(self, db: AsyncSession, hostcomm_client=None, status_cache=None):
        self.db = db
        self.client = hostcomm_client
        self.cache = status_cache

    async def get(self, recipe_id: str, version: int | None = None) -> dict:
        query = select(RecipeVersion).where(RecipeVersion.recipe_id == recipe_id)
        if version is not None:
            query = query.where(RecipeVersion.version == version)
        row = await self.db.scalar(query.order_by(RecipeVersion.version.desc()).limit(1))
        if row is None:
            raise CommandError(404, "recipe_not_found", "配方版本不存在")
        return version_payload(row)

    async def versions(self, recipe_id: str) -> list[dict]:
        rows = await self.db.scalars(
            select(RecipeVersion).where(RecipeVersion.recipe_id == recipe_id).order_by(RecipeVersion.version.desc())
        )
        return [version_payload(row) for row in rows]

    async def list(self, page: int = 1, size: int = 20) -> dict:
        latest = (
            select(RecipeVersion.recipe_id, func.max(RecipeVersion.version).label("version"))
            .group_by(RecipeVersion.recipe_id)
            .subquery()
        )
        query = select(RecipeVersion).join(
            latest, (RecipeVersion.recipe_id == latest.c.recipe_id) & (RecipeVersion.version == latest.c.version)
        )
        rows = await self.db.scalars(
            query.order_by(RecipeVersion.created_at.desc()).offset((page - 1) * size).limit(size)
        )
        total = await self.db.scalar(select(func.count()).select_from(latest))
        return {"items": [version_payload(row) for row in rows], "page": page, "size": size, "total": total}

    async def save(
        self,
        definition: RecipeDefinition | dict,
        *,
        operator_id: str,
        role: str,
        recipe_id: str | None = None,
        client_ip: str | None = None,
    ) -> dict:
        check_permission("set_parameters", role)
        model = definition_model(definition)
        if recipe_id is None:
            recipe_id, version = uuid.uuid4().hex, 1
        else:
            previous = await self.get(recipe_id)
            version = previous["version"] + 1
        row = RecipeVersion(
            recipe_id=recipe_id,
            version=version,
            digest=model.digest(),
            definition_json=canonical_json(model.model_dump()),
            created_at=now_iso(),
            created_by=operator_id,
        )
        self.db.add(row)
        try:
            # 审计与定义在同一数据库提交；并发写相同版本由主键拒绝，不覆盖旧版本。
            await audit_action(
                self.db,
                operator_id=operator_id,
                role=role,
                action_type="save_recipe",
                params={"recipe_id": recipe_id, "version": version, "digest": row.digest},
                result="saved",
                client_ip=client_ip,
            )
        except IntegrityError as exc:
            await self.db.rollback()
            raise CommandError(409, "recipe_version_conflict", "配方已被同时更新，请读取最新版本后重试") from exc
        return version_payload(row)

    async def validate_bundle(self, bundle: Any, snapshot: dict) -> dict:
        if not isinstance(bundle, dict) or set(bundle) != {"recipe_id", "version", "digest", "definition"}:
            raise CommandError(422, "invalid_recipe_bundle", "配方必须携带完整且唯一的身份、版本、摘要和定义")
        if not isinstance(bundle["recipe_id"], str) or type(bundle["version"]) is not int or bundle["version"] < 1:
            raise CommandError(422, "invalid_recipe_bundle", "配方身份或版本无效")
        saved = await self.get(bundle["recipe_id"], bundle["version"])
        model = definition_model(bundle["definition"])
        if bundle["digest"] != saved["digest"] or model.digest() != saved["digest"]:
            raise CommandError(409, "recipe_digest_mismatch", "配方内容与保存的版本不一致")
        # 固件安全配置为快照顶层只读字段，绝不接受来自调用者的覆盖值。
        verdict = validate_for_device(
            model, snapshot.get("safety_profile"), list(getattr(self.client, "capabilities", []))
        )
        if not verdict["executable"]:
            raise CommandError(409, "recipe_not_executable", "; ".join(verdict["errors"]))
        return {**bundle_payload(saved), "validation": verdict}

    async def validate(self, recipe_id: str, version: int) -> dict:
        from app.services.parameter_service import ParameterService

        recipe = await self.get(recipe_id, version)
        capabilities = list(getattr(self.client, "capabilities", []))
        if "recipe_v1" not in capabilities:
            return validate_for_device(definition_model(recipe["definition"]), None, capabilities)
        snapshot = await ParameterService(self.client, self.cache).get_parameters()
        return validate_for_device(definition_model(recipe["definition"]), snapshot.get("safety_profile"), capabilities)

    async def activate(
        self,
        recipe_id: str,
        version: int,
        *,
        operator_id: str,
        role: str,
        operation_id: str | None,
        client_ip: str | None = None,
    ) -> dict:
        from app.services.parameter_service import ParameterService

        check_permission("set_parameters", role)
        recipe = await self.get(recipe_id, version)
        # 校验在ParameterService的写锁/持久操作内再次执行；重复操作不会重读或重发。
        return await ParameterService(self.client, self.cache).patch_parameters(
            {"recipe": bundle_payload(recipe)},
            operator_id=operator_id,
            role=role,
            operation_id=operation_id,
            client_ip=client_ip,
            db_session=self.db,
        )
