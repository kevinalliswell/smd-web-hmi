"""配方版本与激活API；设备写入统一经过ParameterService。"""

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import DbDep, UserDep, get_current_user, get_hostcomm_client, require_role
from app.api.operation_api import OPERATION_ERRORS, operation_http_error, request_operation_id
from app.api.schemas import ok
from app.api.validation import Page, PageSize
from app.services.cache import status_cache
from app.services.recipe_definition import RecipeDefinition, standard_template
from app.services.recipe_service import RecipeService

router = APIRouter(prefix="/api/recipes", tags=["recipes"], dependencies=[Depends(get_current_user)])


class SaveRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    definition: RecipeDefinition


class RecipeSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1, strict=True)


class ActivateRecipe(RecipeSelection):
    operation_id: str | None = Field(default=None, min_length=1, max_length=128)


def service(request: Request, db: DbDep) -> RecipeService:
    return RecipeService(db, get_hostcomm_client(request), status_cache)


@router.get("/template/standard")
async def template():
    return ok({"definition": standard_template().model_dump(), "executable": False, "requires_device_validation": True})


@router.get("")
async def list_recipes(db: DbDep, page: Page = 1, size: PageSize = 20):
    return ok(await RecipeService(db).list(page, size))


@router.post("", dependencies=[Depends(require_role("admin"))])
async def save_recipe(body: SaveRecipe, user: UserDep, db: DbDep, request: Request):
    try:
        return ok(
            await RecipeService(db).save(
                body.definition,
                operator_id=user.username,
                role=user.role,
                client_ip=request.client.host if request.client else None,
            )
        )
    except OPERATION_ERRORS as exc:
        raise operation_http_error(exc) from exc


@router.get("/{recipe_id}/versions")
async def versions(recipe_id: str, db: DbDep):
    return ok({"versions": await RecipeService(db).versions(recipe_id)})


@router.get("/{recipe_id}")
async def get_recipe(recipe_id: str, db: DbDep, version: int | None = Query(default=None, ge=1)):
    try:
        return ok(await RecipeService(db).get(recipe_id, version))
    except OPERATION_ERRORS as exc:
        raise operation_http_error(exc) from exc


@router.put("/{recipe_id}", dependencies=[Depends(require_role("admin"))])
async def revise_recipe(recipe_id: str, body: SaveRecipe, user: UserDep, db: DbDep, request: Request):
    try:
        return ok(
            await RecipeService(db).save(
                body.definition,
                recipe_id=recipe_id,
                operator_id=user.username,
                role=user.role,
                client_ip=request.client.host if request.client else None,
            )
        )
    except OPERATION_ERRORS as exc:
        raise operation_http_error(exc) from exc


@router.post("/{recipe_id}/validate")
async def validate_recipe(recipe_id: str, body: RecipeSelection, request: Request, db: DbDep):
    try:
        return ok(await service(request, db).validate(recipe_id, body.version))
    except OPERATION_ERRORS as exc:
        raise operation_http_error(exc) from exc


@router.post("/{recipe_id}/activate", dependencies=[Depends(require_role("admin"))])
async def activate_recipe(
    recipe_id: str,
    body: ActivateRecipe,
    user: UserDep,
    request: Request,
    db: DbDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return ok(
            await service(request, db).activate(
                recipe_id,
                body.version,
                operator_id=user.username,
                role=user.role,
                operation_id=request_operation_id(body.operation_id, idempotency_key),
                client_ip=request.client.host if request.client else None,
            )
        )
    except OPERATION_ERRORS as exc:
        raise operation_http_error(exc) from exc
