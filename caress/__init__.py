"""JAV metadata for Emby."""

__version__ = "2.0.0"

from .models import ScrapeRequest, ScrapeResult
from .scraper import Scraper
from .settings import Settings

__all__ = ["ScrapeRequest", "ScrapeResult", "Scraper", "Settings"]
