"""Exact-path Emby association and retryable, content-based synchronization."""
import asyncio
import base64
import hashlib
import io
import json
import re
from dataclasses import dataclass

import httpx
from PIL import Image

from .portraits import actor_image
from .storage import target_folder


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def work_images(folder, code):
    items = []
    if not folder.is_dir():
        return items
    for path in folder.iterdir():
        match = re.fullmatch(re.escape(code) + r"-(poster|fanart([1-9][0-9]*)?)", path.stem)
        if match and path.is_file() and not path.is_symlink() and path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp', '.gif'}:
            items.append(('Primary' if match[1] == 'poster' else 'Backdrop', int(match[2] or 0), path))
    items.sort(key=lambda item: (item[0], item[1]))
    roles = [(kind, index) for kind, index, _ in items]
    if len(roles) != len(set(roles)):
        raise ValueError('作品图片角色重复')
    result, backdrop = [], 0
    for kind, _, path in items:
        result.append((kind, backdrop if kind == 'Backdrop' else 0, path))
        if kind == 'Backdrop':
            backdrop += 1
    return result


def sync_files(output, code, actors):
    folder = target_folder(output, code)
    images = [(kind, number, str(path), hashlib.sha256(path.read_bytes()).hexdigest())
              for kind, number, path in work_images(folder, code)]
    portraits = [(actor.name, str(path), hashlib.sha256(path.read_bytes()).hexdigest())
                 for actor in actors if (path := actor_image(output, actor.name))]
    return images, portraits


def image_pixels_match(expected_bytes, actual_bytes):
    with Image.open(io.BytesIO(expected_bytes)) as expected, Image.open(io.BytesIO(actual_bytes)) as actual:
        return actual.size == expected.size and actual.convert("RGBA").tobytes() == expected.convert("RGBA").tobytes()


def sync_error(exc):
    if isinstance(exc, httpx.HTTPStatusError):
        return f'Emby 请求失败（HTTP {exc.response.status_code}）'
    if isinstance(exc, httpx.TimeoutException):
        return 'Emby 请求超时'
    if isinstance(exc, httpx.HTTPError):
        return '无法连接 Emby'
    return str(exc)


def field_value(key, value):
    if key in ('People', 'Studios', 'GenreItems', 'TagItems'):
        values = [(item['Name'], item.get('Type')) for item in value or []]
        return values if key == 'People' else sorted(values)
    if key == 'PremiereDate':
        return value[:10] if value else None
    if key in ('OriginalTitle', 'Overview'):
        return value or ''
    return value


def fields_match(actual, expected):
    return all(field_value(key, actual.get(key)) == field_value(key, value) for key, value in expected.items())


@dataclass(frozen=True)
class ImageUpload:
    kind: str
    index: int
    file: str
    digest: str


@dataclass(frozen=True)
class SyncPlan:
    """Pure desired changes; constructing a plan performs no I/O."""
    item_id: str
    metadata: dict
    update_metadata: bool
    managed_tags: list[str]
    managed_people: list[tuple[str, str]]
    uploads: tuple[ImageUpload, ...]
    deletions: tuple[str, ...]
    portraits: tuple[tuple[str, str, str], ...]


def plan_sync(ident, metadata, original, item_state, media, images, portraits):
    labels = (["中文字幕"] if any(row["subtitles"] for row in media) else []) + (
        ["无码破解"] if any(row["uncensored"] for row in media) else [])
    tags = list(dict.fromkeys([*metadata.tags, *labels]))
    people = [{"Name": actor.name, "Type": "Actor"} for actor in metadata.actors] + [
        {"Name": name, "Type": "Director"} for name in metadata.directors]
    managed_people = [(person["Name"], person["Type"]) for person in people]
    prior_people = {tuple(person) for person in item_state.get("managed_people", [])}
    people += [person for person in original.get("People", [])
               if (person["Name"], person.get("Type")) not in prior_people | set(managed_people)]
    payload = {"Name": metadata.title, "OriginalTitle": metadata.original_title or "",
        "Overview": metadata.plot or "", "PremiereDate": metadata.release_date.isoformat() + "T00:00:00Z" if metadata.release_date else None,
        "ProductionYear": metadata.year or (metadata.release_date.year if metadata.release_date else None),
        "GenreItems": [{"Name": name} for name in metadata.genres],
        "Studios": [{"Name": metadata.studio}] if metadata.studio else [], "People": people,
        "ProviderIds": {**original.get("ProviderIds", {}), "caress": metadata.code},
        "TagItems": [{"Name": name} for name in dict.fromkeys([
            tag["Name"] for tag in original.get("TagItems", []) if tag["Name"] not in item_state.get("managed_tags", [])] + tags)],
        "LockData": True}
    saved_images = item_state.get("images", {})
    desired = {f"{kind}:{index}" for kind, index, _, _ in images}
    uploads = tuple(ImageUpload(kind, index, file, digest) for kind, index, file, digest in images
                    if saved_images.get(f"{kind}:{index}") != digest)
    deletions = tuple(sorted((key for key in saved_images if desired and key not in desired),
                            key=lambda key: int(key.split(":")[1]), reverse=True))
    return SyncPlan(ident, payload, not fields_match(original, payload), tags, managed_people,
                    uploads, deletions, tuple(portraits))


class EmbyClient:
    def __init__(self, settings):
        self.settings = settings
        self.user = None
        self.client = httpx.AsyncClient(base_url=settings.url.rstrip('/') + '/emby/',
            headers={'X-Emby-Token': settings.api_key}, timeout=30)

    async def request(self, method, path, **kwargs):
        response = await self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None

    async def close(self):
        await self.client.aclose()

    async def test(self):
        info = await self.request('GET', 'System/Info')
        return {'valid': True, 'server': info['ServerName'], 'version': info['Version']}

    async def index(self):
        result = {}
        start = 0
        while True:
            data = await self.request('GET', 'Items', params={'Recursive': 'true', 'IncludeItemTypes': 'Movie',
                'Fields': 'Path,MediaSources', 'StartIndex': start, 'Limit': 500})
            for item in data['Items']:
                if item.get('Path'):
                    result.setdefault(item['Path'], set()).add(str(item['Id']))
                for media in item.get('MediaSources', []):
                    path = media.get('Path', '')
                    if path.startswith('/'):
                        result.setdefault(path, set()).add(str(media.get('ItemId') or item['Id']))
                if item.get('PartCount', 1) > 1:
                    parts = await self.request('GET', f"Videos/{item['Id']}/AdditionalParts")
                    for part in parts.get('Items', []):
                        if part.get('Path'):
                            result.setdefault(part['Path'], set()).add(str(part['Id']))
            start += len(data['Items'])
            if start >= data['TotalRecordCount'] or not data['Items']:
                return result

    async def item(self, ident):
        if self.user is None:
            users = await self.request('GET', 'Users')
            admin = next((user for user in users if user.get('Policy', {}).get('IsAdministrator')), None)
            if admin is None:
                raise ValueError('Emby API Key 无管理员用户')
            self.user = admin['Id']
        return await self.request('GET', f'Users/{self.user}/Items/{ident}')

    async def upload(self, ident, kind, data, index=0):
        with Image.open(io.BytesIO(data)) as image:
            mime = Image.MIME[image.format]
        await self.request('POST', f'Items/{ident}/Images/{kind}/{index}',
            content=base64.b64encode(data), headers={'Content-Type': mime})
        saved = await self.request('GET', f'Items/{ident}/Images')
        if not any(row['ImageType'] == kind and row.get('ImageIndex', 0) == index and row.get('Width', 0) > 0 for row in saved):
            raise ValueError('Emby 图片回读验证失败')
        response = await self.client.get(f"Items/{ident}/Images/{kind}/{index}", params={"Format": "original", "EnableImageEnhancers": "false"}, headers={"Cache-Control": "no-cache"})
        response.raise_for_status()
        if not await asyncio.to_thread(image_pixels_match, data, response.content):
            raise ValueError("Emby 图片内容回读不一致")


class EmbySync:
    def __init__(self, service):
        self.service = service
        self.lock = asyncio.Lock()

    async def apply(self, client, plan, original, item_state, server):
        from pathlib import Path

        runtime = self.service.runtime
        current = original
        if plan.update_metadata:
            await client.request("POST", f"Items/{plan.item_id}", json={**original, **plan.metadata})
            current = await client.item(plan.item_id)
            if not fields_match(current, plan.metadata):
                raise ValueError("Emby 元数据回读不一致")
        item_state.update(managed_tags=plan.managed_tags, managed_people=plan.managed_people)
        runtime.save_item_sync(server, plan.item_id, item_state)
        images = dict(item_state.get("images", {}))
        for upload in plan.uploads:
            data = await asyncio.to_thread(Path(upload.file).read_bytes)
            if hashlib.sha256(data).hexdigest() != upload.digest:
                raise ValueError("同步期间图片已变化，请重试")
            await client.upload(plan.item_id, upload.kind, data, upload.index)
            images[f"{upload.kind}:{upload.index}"] = upload.digest
            item_state["images"] = dict(images)
            runtime.save_item_sync(server, plan.item_id, item_state)
        for key in plan.deletions:
            kind, number = key.split(":")
            await client.request("DELETE", f"Items/{plan.item_id}/Images/{kind}/{number}")
            saved = await client.request("GET", f"Items/{plan.item_id}/Images")
            if any(row["ImageType"] == kind and row.get("ImageIndex", 0) == int(number) for row in saved):
                raise ValueError("Emby 图片删除回读不一致")
            images.pop(key)
            item_state["images"] = dict(images)
            runtime.save_item_sync(server, plan.item_id, item_state)
        people = {person["Name"]: person.get("Id") for person in current.get("People", []) if person.get("Type") == "Actor"}
        for name, file, digest in plan.portraits:
            person = people.get(name)
            if not person:
                raise ValueError("Emby 未返回演员 ID：" + name)
            if runtime.portrait_sync(server, person) != digest:
                data = await asyncio.to_thread(Path(file).read_bytes)
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError("同步期间头像已变化，请重试")
                await client.upload(person, "Primary", data)
                runtime.save_portrait_sync(server, person, digest)

    async def sync(self, code, *, relink=False, force=False):
        service = self.service
        metadata = service.runtime.metadata(code)
        if metadata is None:
            return []
        async with self.lock:
            settings = service.settings.emby
            rows = service.runtime.media(code)
            if not settings.url or not settings.api_key:
                for row in rows:
                    service.runtime.save_sync(row['id'], {'status': 'pending', 'error': '尚未配置 Emby'})
                return service.runtime.media(code)
            client = EmbyClient(settings)
            pending = rows
            try:
                image_inputs, portrait_inputs = await asyncio.to_thread(sync_files, service.output, code, metadata.actors)
                content = fingerprint({'metadata': metadata.model_dump(mode='json'), 'images': image_inputs,
                    'portraits': portrait_inputs, 'media': [row['id'] for row in rows],
                    'configuration': settings.model_dump(mode='json')})
                pending = [row for row in rows if force or relink or row.get('sync', {}).get('status') != 'synced' or row.get('sync', {}).get('content') != content]
                if not pending:
                    return rows
                index = await client.index()
                for row in pending:
                    state = row.get('sync', {})
                    try:
                        matches = index.get(row['path'], set())
                        if len(matches) != 1:
                            state.update(status='pending', error='Emby 未收录此路径' if not matches else 'Emby 路径对应多个项目')
                            service.runtime.save_sync(row['id'], state)
                            continue
                        ident = next(iter(matches))
                        if ident != state.get('item_id') or state.get('server') != settings.url:
                            state = {}
                        state.update(item_id=ident, server=settings.url, status="syncing", error=None)
                        service.runtime.save_sync(row["id"], state)
                        original = await client.item(ident)
                        server = original.get('ServerId') or settings.url
                        item_state = service.runtime.item_sync(server, ident)
                        associated = [media for media in rows if index.get(media['path']) == {ident}]
                        plan = plan_sync(ident, metadata, original, item_state, associated, image_inputs, portrait_inputs)
                        await self.apply(client, plan, original, item_state, server)
                        state.update(status='synced', error=None, content=content)
                    except (httpx.HTTPError, ValueError, OSError) as exc:
                        state.update(status='failed', error=sync_error(exc))
                    service.runtime.save_sync(row['id'], state)
                return service.runtime.media(code)
            except asyncio.CancelledError:
                for row in service.runtime.media(code):
                    state = row['sync']
                    if state.get('status') == 'syncing':
                        state.update(status='pending', error='同步已取消')
                        service.runtime.save_sync(row['id'], state)
                raise
            except (httpx.HTTPError, ValueError, OSError) as exc:
                for row in pending:
                    state = row.get('sync', {})
                    state.update(status='failed', error=sync_error(exc))
                    service.runtime.save_sync(row['id'], state)
                return service.runtime.media(code)
            finally:
                await client.close()
