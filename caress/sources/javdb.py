from urllib.parse import urlparse

from ..logging import report_progress
from ..models import SourceId, Status
from .base import Source
from .javdb_api import JavDBApi
from .javdb_web import JavDBWeb


class JavDB(Source):
    """One public source: App first, webpage fallback."""

    id = SourceId.JAVDB
    default_url = "https://javdb.com"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.app = JavDBApi(*args, **kwargs)
        self.web = JavDBWeb(*args, **kwargs)

    async def fetch(self, request):
        if request.source_url and urlparse(request.source_url).netloc.lower().removesuffix(
            ":443"
        ) == urlparse(self.web.base).netloc.lower().removesuffix(":443"):
            return await self.web.fetch(request)
        report_progress("source", source=self.id, message="访问接口")
        result = await self.app.fetch(request)
        if result.status in (Status.FOUND, Status.IDENTITY_MISMATCH):
            return result
        if request.source_url:
            # Explicit URLs are never silently replaced with a different query.
            return result
        report_progress("source", source=self.id, message="打开网页")
        fallback = await self.web.fetch(request)
        fallback.warnings.insert(0, f"JavDB App: {result.status}; used webpage fallback")
        return fallback
