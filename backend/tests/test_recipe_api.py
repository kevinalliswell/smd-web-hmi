"""真实ASGI路由的配方权限、版本和验证响应。"""

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import CurrentUser, get_current_user
from app.api.routes.recipes import router
from app.db.database import get_db


async def test_recipe_api_versions_permissions_and_missing_capability(db_session):
    app = FastAPI()
    app.include_router(router)
    user = CurrentUser("admin", "admin")
    app.dependency_overrides[get_current_user] = lambda: user

    async def database():
        yield db_session

    app.dependency_overrides[get_db] = database
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        template = (await client.get("/api/recipes/template/standard")).json()["data"]
        assert template["requires_device_validation"]
        created = await client.post("/api/recipes", json={"definition": template["definition"]})
        assert created.status_code == 200
        recipe = created.json()["data"]
        uri = "/api/recipes/" + recipe["recipe_id"]
        revised = await client.put(uri, json={"definition": {**template["definition"], "name": "Next"}})
        assert revised.json()["data"]["version"] == 2
        assert (await client.get(uri, params={"version": 1})).json()["data"]["digest"] == recipe["digest"]
        assert (await client.get("/api/recipes")).json()["data"]["total"] == 1
        assert len((await client.get(uri + "/versions")).json()["data"]["versions"]) == 2
        verdict = (await client.post(uri + "/validate", json={"version": 1})).json()["data"]
        assert verdict["executable"] is False
        assert "device_missing_recipe_v1" in verdict["errors"]
        user.role = "observer"
        assert (
            await client.post(uri + "/activate", json={"version": 1, "operation_id": "readonly"})
        ).status_code == 403
        assert (await client.put(uri, json={"definition": template["definition"]})).status_code == 403
