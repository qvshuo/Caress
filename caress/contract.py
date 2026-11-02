# Runtime/source diagnostics are deliberately outside the editable NFO contract.
NFO_FIELDS = {
    "title": "title", "original_title": "originaltitle", "plot": "plot",
    "release_date": "premiered", "runtime_minutes": "runtime", "actors": "actor",
    "directors": "director", "studio": "studio", "series": "set",
    "genres": "genre", "tags": "tag", "code": "uniqueid",
}
EDITABLE_FIELDS = tuple(field for field in NFO_FIELDS if field not in {"code", "original_title"})
