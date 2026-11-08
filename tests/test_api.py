import httpx
import pytest

from caress.api import create_app
from caress.models import WorkMetadata
from caress.settings import Settings


@pytest.mark.asyncio
async def test_fresh_configuration_edit_full_form_and_restart(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<html>Caress</html>")
    media = tmp_path / "media"
    media.mkdir()
    (media / "ABC-123-C.strm").touch()
    database = tmp_path / "data" / "caress.sqlite3"
    app = create_app(Settings(database=database), static_directory=web)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://caress.test") as client:
        assert (await client.get("/api/health")).json()["storage"] == "sqlite"
        assert not (await client.get("/api/settings")).json()["configured"]
        response = await client.put("/api/settings", json={"media_directories": [str(media)]})
        assert response.status_code == 200, response.text
        assert response.json()["configured"]
        assert (await client.get("/api/works")).json()[0]["code"] == "ABC-123"
        body = WorkMetadata(code="ABC-123", title="manual title").model_dump(mode="json", exclude={"code"})
        response = await client.put("/api/works/ABC-123/metadata", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["metadata"]["title"] == "manual title"
        assert response.json()["media"][0]["sync"]["status"] == "pending"
        assert (await client.put("/api/works/ABC-123/metadata", json={"title": "incomplete"})).status_code == 422
    restored = create_app(Settings.load(database), static_directory=web)
    async with restored.router.lifespan_context(restored), httpx.AsyncClient(transport=httpx.ASGITransport(restored), base_url="http://caress.test") as client:
        detail = (await client.get("/api/works/detail", params={"code": "ABC-123"})).json()
        assert detail["metadata"]["title"] == "manual title"
        assert detail["status"] == "scraped"
        assert detail["selection"]["manual_fields"] == ["title"]
