from ..logging import report_progress
from ..models import SourceId
from .base import Source
from .javdb_api import JavDBApi
from .javdb_web import JavDBWeb


class JavDB(Source):
    """JavDB webpage metadata and verified public actor details."""

    id = SourceId.JAVDB
    default_url = "https://javdb.com"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.app = JavDBApi(*args, **kwargs)
        self.web = JavDBWeb(*args, **kwargs)

    async def fetch(self, request):
        report_progress("source", source=self.id, message="打开网页")
        return await self.web.fetch(request)
