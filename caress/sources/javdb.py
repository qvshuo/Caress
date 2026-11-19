from ..models import SourceId
from .base import Source
from .javdb_web import JavDBWeb


class JavDB(Source):
    """JavDB webpage metadata."""

    id = SourceId.JAVDB
    default_url = "https://javdb.com"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.web = JavDBWeb(*args, **kwargs)

    async def fetch(self, request):
        return await self.web.fetch(request)
