"""Private Ablage fuer Gesundheitsdokumente.

Objektschluessel werden ausschliesslich serverseitig erzeugt. Der Browser
erhaelt weder einen Bucket-Pfad noch dauerhafte URLs; Downloads laufen durch
die bereits haushalts- und personenautorisierte API.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx

from app.core.config import Settings


class HealthDocumentStorageError(RuntimeError):
    pass


class HealthDocumentStorage:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def uses_supabase(self) -> bool:
        return bool(
            self.settings.storage_backend == "supabase"
            and self.settings.supabase_url
            and self.settings.supabase_service_role_key
        )

    def _local_path(self, object_key: str) -> Path:
        root = Path(self.settings.health_documents_local_path).resolve()
        target = (root / object_key).resolve()
        if root != target and root not in target.parents:
            raise HealthDocumentStorageError("ungueltiger Objektschluessel")
        return target

    def _supabase_headers(self, mime_type: str | None = None) -> dict[str, str]:
        key = self.settings.supabase_service_role_key
        headers = {"Authorization": f"Bearer {key}", "apikey": key}
        if mime_type:
            headers["Content-Type"] = mime_type
            headers["x-upsert"] = "false"
        return headers

    def _supabase_url(self, object_key: str) -> str:
        base = self.settings.supabase_url.rstrip("/")
        bucket = self.settings.health_documents_bucket
        return f"{base}/storage/v1/object/{bucket}/{object_key}"

    async def put(self, object_key: str, content: bytes, mime_type: str) -> None:
        if self.uses_supabase:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        self._supabase_url(object_key),
                        content=content,
                        headers=self._supabase_headers(mime_type),
                    )
                if response.status_code not in {200, 201}:
                    raise HealthDocumentStorageError(
                        f"Supabase Storage antwortet mit HTTP {response.status_code}"
                    )
            except httpx.HTTPError as exc:
                raise HealthDocumentStorageError("Supabase Storage ist nicht erreichbar") from exc
            return

        target = self._local_path(object_key)

        def write_local() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise HealthDocumentStorageError("Objekt existiert bereits")
            temporary = target.with_suffix(target.suffix + ".uploading")
            try:
                temporary.write_bytes(content)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)

        await asyncio.to_thread(write_local)

    async def get(self, object_key: str) -> bytes:
        if self.uses_supabase:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(
                        self._supabase_url(object_key), headers=self._supabase_headers()
                    )
                if response.status_code == 404:
                    raise FileNotFoundError(object_key)
                if response.status_code != 200:
                    raise HealthDocumentStorageError(
                        f"Supabase Storage antwortet mit HTTP {response.status_code}"
                    )
                return response.content
            except httpx.HTTPError as exc:
                raise HealthDocumentStorageError("Supabase Storage ist nicht erreichbar") from exc

        target = self._local_path(object_key)

        def read_local() -> bytes:
            if not target.is_file():
                raise FileNotFoundError(object_key)
            return target.read_bytes()

        return await asyncio.to_thread(read_local)

    async def delete(self, object_key: str) -> None:
        if self.uses_supabase:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.delete(
                        self._supabase_url(object_key), headers=self._supabase_headers()
                    )
                if response.status_code not in {200, 204, 404}:
                    raise HealthDocumentStorageError(
                        f"Supabase Storage antwortet mit HTTP {response.status_code}"
                    )
            except httpx.HTTPError as exc:
                raise HealthDocumentStorageError("Supabase Storage ist nicht erreichbar") from exc
            return
        await asyncio.to_thread(self._local_path(object_key).unlink, missing_ok=True)

    async def check(self) -> tuple[str, str]:
        if self.uses_supabase:
            base = self.settings.supabase_url.rstrip("/")
            bucket = self.settings.health_documents_bucket
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.get(
                        f"{base}/storage/v1/bucket/{bucket}", headers=self._supabase_headers()
                    )
                if response.status_code == 200:
                    return "ok", "privater Gesundheitsdokument-Bucket erreichbar"
                return "degraded", f"Gesundheitsdokument-Bucket HTTP {response.status_code}"
            except httpx.HTTPError:
                return "degraded", "Gesundheitsdokument-Bucket nicht erreichbar"

        try:
            root = Path(self.settings.health_documents_local_path).resolve()
            await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
            return "ok", "private lokale Gesundheitsdokument-Ablage erreichbar"
        except OSError:
            return "down", "lokale Gesundheitsdokument-Ablage nicht beschreibbar"
