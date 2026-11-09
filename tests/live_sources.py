"""Opt-in source smoke probe. Run in the test Compose container; never writes Emby."""

import asyncio
import json

from caress.models import ScrapeRequest, SourceId
from caress.scraper import Scraper
from caress.settings import Settings

CODES = {
    SourceId.DMM: "SSIS-001",
    SourceId.JAVDB: "SSIS-001",
    SourceId.JAVBUS: "SSIS-001",
    SourceId.FC2: "FC2PPV-4682908",
    SourceId.AVSOX: "010115_001",
    SourceId.AIRAV: "SSIS-001",
    SourceId.ONEPONDO: "010115_001",
    SourceId.CARIBBEANCOM: "012318-589",
    SourceId.HEYZO: "HEYZO-0780",
    SourceId.AVBASE: "SSIS-001",
}


async def main():
    settings = Settings.load()
    async with Scraper(settings) as scraper:

        async def probe(source, code):
            try:
                async with asyncio.timeout(45):
                    result = await scraper.scrape(
                        ScrapeRequest(code=code, source=source)
                    )
            except TimeoutError:
                return {"source": source, "code": code, "status": "timeout"}
            return {
                "source": source,
                "code": code,
                "status": result.status,
                "title": bool(result.metadata and result.metadata.title),
                "artwork": len(result.metadata.artwork) if result.metadata else 0,
            }

        results = await asyncio.gather(
            *(probe(source, code) for source, code in CODES.items())
        )
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
