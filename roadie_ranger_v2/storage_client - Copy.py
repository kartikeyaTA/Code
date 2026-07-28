"""
storage_client.py
─────────────────
Azure Blob Storage + Table Storage client for session logging, usage quotas,
local user accounts, and log file persistence.

Blob Storage
  voicelive-sessions/{entity_id}/{YYYY-MM-DD}/{session_id}.json
  voicelive-logs/{YYYY-MM-DD}/{timestamp}_voicelive.log

Table Storage
  UserQuota    — PartitionKey=entity_id,  RowKey=YYYY-MM-DD
                 columns: sessions_count, minutes_used
  UserRegistry — PartitionKey="local",     RowKey=user_id  (also has password_hash)
                 columns: user_id, first_name, last_name, organisation,
                          password_hash, created_at

entity_id conventions:
  Browser (Microsoft)  : Entra principal ID  (e.g. "a1b2c3d4-...")
  Browser (local acct) : UUID assigned at registration

Authentication uses ChainedTokenCredential (AzureCliCredential locally,
ManagedIdentityCredential in Container Apps). No storage account keys.

If AZURE_STORAGE_ACCOUNT_URL is not set the client silently no-ops every
operation so local development continues to work without Azure storage.
"""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from azure.core import MatchConditions
    from azure.data.tables.aio import TableServiceClient
    from azure.storage.blob.aio import BlobServiceClient
    _AZURE_AVAILABLE = True
except ImportError:
    _AZURE_AVAILABLE = False
    logger.warning("azure-storage-blob / azure-data-tables not installed — storage disabled")

_BLOB_CONTAINER = "RoadieRangerSessions"
_LOG_BLOB_CONTAINER = "RoadieRangerLogs"
_QUOTA_TABLE = "RoadieRangerUserQuota"
_USER_REGISTRY_TABLE = "RoadieRangerUserRegistry"
_MAX_RETRY = 5


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _ascii_meta(value: str, max_len: int = 256) -> str:
    """Strip non-ASCII and control characters so blob metadata stays valid HTTP header values."""
    return "".join(c for c in value if 0x20 <= ord(c) < 0x7F)[:max_len]


class StorageClient:
    """
    Thin async wrapper around Blob + Table Storage.

    Construct via StorageClient.create() which reads AZURE_STORAGE_ACCOUNT_URL
    from the environment. Returns a no-op stub if the URL is not configured.
    """

    def __init__(
        self,
        blob_service: Optional["BlobServiceClient"],
        table_service: Optional["TableServiceClient"],
    ) -> None:
        self._blob = blob_service
        self._table = table_service

    # ── factory ───────────────────────────────────────────────────────────────

    @classmethod
    def create(cls, credential) -> "StorageClient":
        account_url = os.environ.get("AZURE_STORAGE_ACCOUNT_URL", "").strip()
        if not account_url or not _AZURE_AVAILABLE:
            if not account_url:
                logger.info("AZURE_STORAGE_ACCOUNT_URL not set — session storage disabled")
            return cls(None, None)

        table_url = account_url.replace(".blob.", ".table.")
        blob_svc = BlobServiceClient(account_url=account_url, credential=credential)
        table_svc = TableServiceClient(endpoint=table_url, credential=credential)
        logger.info("StorageClient initialised — blob=%s", account_url)
        return cls(blob_svc, table_svc)

    @property
    def enabled(self) -> bool:
        return self._blob is not None

    # ── Initialise infrastructure ─────────────────────────────────────────────

    async def ensure_infrastructure(self) -> None:
        """
        Create blob containers and tables if they don't already exist.
        Safe to call on every startup — all operations are idempotent.
        """
        if not self.enabled:
            return

        # Blob containers
        for container_name in (_BLOB_CONTAINER, _LOG_BLOB_CONTAINER):
            try:
                container = self._blob.get_container_client(container_name)
                await container.create_container()
                logger.info("Created blob container: %s", container_name)
            except Exception as exc:
                if "ContainerAlreadyExists" in type(exc).__name__ or "409" in str(exc):
                    pass  # already exists — fine
                else:
                    logger.warning("Could not create container %s: %s", container_name, exc)

        # Tables
        for table_name in (_QUOTA_TABLE, _USER_REGISTRY_TABLE):
            try:
                await self._table.create_table(table_name)
                logger.info("Created table: %s", table_name)
            except Exception as exc:
                if "TableAlreadyExists" in type(exc).__name__ or "409" in str(exc) or "Conflict" in str(exc):
                    pass  # already exists — fine
                else:
                    logger.warning("Could not create table %s: %s", table_name, exc)

    # ── Blob: session upload ──────────────────────────────────────────────────

    async def upload_session(self, session_data: dict) -> None:
        """
        Upload a completed session JSON to Blob Storage.
        Path: voicelive-sessions/{entity_id}/{YYYY-MM-DD}/{session_id}.json

        Blob metadata is set so list_user_sessions can return summaries without
        downloading full JSON bodies.
        """
        if not self.enabled:
            return

        session_id = session_data.get("session_id", "unknown")
        entity_id = session_data.get("user_id", "unknown")
        date_str = (session_data.get("started_at", "")[:10] or _today_utc())

        blob_name = f"{entity_id}/{date_str}/{session_id}.json"
        payload = json.dumps(session_data, ensure_ascii=False, indent=2).encode("utf-8")

        # Build metadata for fast sidebar listing (no need to download full blob)
        turns = session_data.get("turns", [])
        user_turns = [t for t in turns if t.get("role") == "user"]
        preview = (user_turns[0]["text"][:80] if user_turns else "No messages yet")
        metadata = {
            "started_at": session_data.get("started_at", ""),
            "turn_count": str(len(turns)),
            "preview": _ascii_meta(preview),
            "user_name": _ascii_meta(session_data.get("user_name", "")),
        }

        try:
            container = self._blob.get_container_client(_BLOB_CONTAINER)
            await container.upload_blob(
                name=blob_name,
                data=payload,
                overwrite=True,
                content_type="application/json",
                metadata=metadata,
            )
            logger.info("Session uploaded to blob: %s", blob_name)
        except Exception:
            logger.exception("Failed to upload session blob %s — continuing", blob_name)

    # ── Blob: session listing ─────────────────────────────────────────────────

    async def list_user_sessions(self, user_id: str) -> list:
        """
        List session summaries for user_id using blob metadata — no content download.
        Returns list of {session_id, started_at, preview, turn_count} sorted newest-first.
        """
        if not self.enabled:
            return []

        results = []
        try:
            container = self._blob.get_container_client(_BLOB_CONTAINER)
            async for blob in container.list_blobs(
                name_starts_with=f"{user_id}/",
                include=["metadata"],
            ):
                meta = blob.metadata or {}
                parts = blob.name.split("/")
                if len(parts) < 3:
                    continue
                session_id = parts[2].removesuffix(".json")
                results.append({
                    "session_id": session_id,
                    "started_at": meta.get("started_at", ""),
                    "preview": meta.get("preview", "No messages yet"),
                    "turn_count": int(meta.get("turn_count", "0")),
                })
        except Exception:
            logger.exception("Failed to list sessions for user %s", user_id)

        results.sort(key=lambda x: x.get("started_at", ""), reverse=True)
        return results

    async def get_session_blob(self, user_id: str, session_id: str) -> Optional[dict]:
        """
        Download and return a specific session JSON for user_id.
        Scans blobs under user_id/ prefix to find the matching session_id.
        """
        if not self.enabled:
            return None

        try:
            container = self._blob.get_container_client(_BLOB_CONTAINER)
            async for blob in container.list_blobs(name_starts_with=f"{user_id}/"):
                if blob.name.endswith(f"/{session_id}.json"):
                    client = container.get_blob_client(blob.name)
                    downloader = await client.download_blob()
                    content = await downloader.readall()
                    return json.loads(content)
        except Exception:
            logger.exception("Failed to get session blob %s/%s", user_id, session_id)

        return None

    # ── Blob: log file upload ─────────────────────────────────────────────────

    async def upload_log_file(self, log_path: str, timestamp: str) -> None:
        """
        Upload a local log file to voicelive-logs/{YYYY-MM-DD}/{timestamp}.log
        Called on app shutdown so logs survive container termination.
        """
        if not self.enabled:
            return
        if not os.path.exists(log_path):
            return

        date_str = timestamp[:10]
        blob_name = f"{date_str}/{timestamp}_voicelive.log"

        try:
            with open(log_path, "rb") as f:
                data = f.read()
            container = self._blob.get_container_client(_LOG_BLOB_CONTAINER)
            await container.upload_blob(
                name=blob_name,
                data=data,
                overwrite=True,
                content_type="text/plain",
            )
            logger.info("Log file uploaded to blob: %s", blob_name)
        except Exception:
            logger.exception("Failed to upload log file — continuing")

    # ── Table: UserRegistry — unified for all login methods ──────────────────
    #
    #  PartitionKey = auth_method  ("local" | "microsoft")
    #  RowKey       = user_id      (UUID for local, Entra GUID for Microsoft)
    #
    #  Local rows also carry: password_hash, first_name, last_name, organisation
    #  Microsoft rows carry:  display_name, email (UPN), first_seen, last_seen

    async def create_local_user(self, user_data: dict) -> bool:
        """
        Insert a new local user into UserRegistry.
        Returns False if the email is already registered.
        user_data must contain: user_id, email, first_name, last_name,
                                organisation, password_hash, created_at
        """
        if not self.enabled:
            return True  # dev mode: skip storage

        # Reject duplicate emails before inserting
        existing = await self.get_local_user_by_email(user_data["email"])
        if existing:
            return False

        now = datetime.now(timezone.utc).isoformat()
        entity = {
            "PartitionKey": "local",
            "RowKey": user_data["user_id"],
            "display_name": f"{user_data.get('first_name', '')} {user_data.get('last_name', '')}".strip(),
            "first_seen": now,
            "last_seen": now,
            **user_data,
            "auth_method": "local",  # explicit — user_data never carries this field
        }
        try:
            table = self._table.get_table_client(_USER_REGISTRY_TABLE)
            await table.create_entity(entity)
            logger.info("UserRegistry: local user registered %s", user_data.get("email"))
            return True
        except Exception as exc:
            exc_str = str(exc)
            if "EntityAlreadyExists" in type(exc).__name__ or "409" in exc_str or "Conflict" in exc_str:
                return False
            logger.exception("Failed to create local user %s", user_data.get("email"))
            raise

    async def get_local_user_by_email(self, email: str) -> Optional[dict]:
        """
        Find a local user by email address.
        Queries the 'local' partition — fast at small user counts.
        """
        if not self.enabled:
            return None

        try:
            table = self._table.get_table_client(_USER_REGISTRY_TABLE)
            results = table.query_entities(
                query_filter="PartitionKey eq @pk and email eq @email",
                parameters={"pk": "local", "email": email},
            )
            async for entity in results:
                return dict(entity)
            return None
        except Exception:
            logger.exception("Failed to look up local user by email %s", email)
            return None

    async def record_user_login(
        self,
        user_id: str,
        display_name: str,
        email: str,
        auth_method: str,
        organisation: str = "",
    ) -> None:
        """
        Upsert a UserRegistry row for any authenticated user.
        Creates on first login; updates display_name and last_seen on subsequent ones.
        Local users are already created at registration — this just refreshes last_seen.
        """
        if not self.enabled:
            return

        now = datetime.now(timezone.utc).isoformat()
        table = self._table.get_table_client(_USER_REGISTRY_TABLE)

        try:
            existing = await table.get_entity(partition_key=auth_method, row_key=user_id)
            updated = dict(existing)
            updated["display_name"] = display_name
            updated["email"] = email
            updated["auth_method"] = auth_method  # backfills rows created before this fix
            updated["last_seen"] = now
            await table.update_entity(entity=updated, mode="merge")
        except Exception as exc:
            if "ResourceNotFound" not in type(exc).__name__:
                logger.warning("UserRegistry read error for %s: %s", user_id, exc)
                return
            # First time seeing this user — create the row
            entity = {
                "PartitionKey": auth_method,
                "RowKey": user_id,
                "user_id": user_id,
                "display_name": display_name,
                "email": email,
                "auth_method": auth_method,
                "organisation": organisation,
                "first_seen": now,
                "last_seen": now,
            }
            try:
                await table.create_entity(entity)
                logger.info("UserRegistry: new user recorded %s (%s)", display_name, auth_method)
            except Exception:
                logger.exception("Failed to record user login for %s", user_id)

    # ── Table: quota operations ───────────────────────────────────────────────

    async def _get_quota_entity(self, entity_id: str, date_str: str) -> dict:
        """Return the quota row, or a zeroed default if it doesn't exist yet."""
        try:
            table = self._table.get_table_client(_QUOTA_TABLE)
            entity = await table.get_entity(
                partition_key=entity_id,
                row_key=date_str,
            )
            return dict(entity)
        except Exception as exc:
            if "ResourceNotFound" not in type(exc).__name__:
                logger.warning("Quota read error for %s/%s: %s", entity_id, date_str, exc)
            return {
                "PartitionKey": entity_id,
                "RowKey": date_str,
                "sessions_count": 0,
                "minutes_used": 0,
            }

    async def get_usage(self, entity_id: str, date_str: Optional[str] = None) -> dict:
        """Return current usage for entity_id on date_str (defaults to today UTC)."""
        if not self.enabled:
            return {"sessions_count": 0, "minutes_used": 0}
        date_str = date_str or _today_utc()
        entity = await self._get_quota_entity(entity_id, date_str)
        return {
            "sessions_count": int(entity.get("sessions_count", 0)),
            "minutes_used": int(entity.get("minutes_used", 0)),
        }

    async def check_and_reserve(
        self,
        entity_id: str,
        session_limit: int,
        minutes_limit: int,
        date_str: Optional[str] = None,
    ) -> bool:
        """
        Check whether entity_id is under both limits and atomically increment
        sessions_count if so. Returns True = reserved, False = denied.
        Uses ETag optimistic concurrency; retries up to _MAX_RETRY times.
        """
        if not self.enabled:
            return True

        date_str = date_str or _today_utc()

        for attempt in range(_MAX_RETRY):
            entity = await self._get_quota_entity(entity_id, date_str)
            sessions = int(entity.get("sessions_count", 0))
            minutes = int(entity.get("minutes_used", 0))

            if sessions >= session_limit:
                logger.warning(
                    "Quota DENIED (sessions) entity=%s date=%s used=%d limit=%d",
                    entity_id, date_str, sessions, session_limit,
                )
                return False

            if minutes >= minutes_limit:
                logger.warning(
                    "Quota DENIED (minutes) entity=%s date=%s used=%d limit=%d",
                    entity_id, date_str, minutes, minutes_limit,
                )
                return False

            updated = dict(entity)
            updated["sessions_count"] = sessions + 1

            try:
                table = self._table.get_table_client(_QUOTA_TABLE)
                etag = entity.get("etag") or entity.get("@odata.etag")
                if etag and sessions > 0:
                    await table.update_entity(
                        entity=updated,
                        match_condition=MatchConditions.IfNotModified,
                        etag=etag,
                    )
                else:
                    await table.upsert_entity(entity=updated)

                logger.info(
                    "Quota reserved entity=%s date=%s sessions=%d/%d minutes=%d/%d",
                    entity_id, date_str, sessions + 1, session_limit, minutes, minutes_limit,
                )
                return True

            except Exception as exc:
                if "ConditionNotMet" in type(exc).__name__ or "ResourceModified" in str(exc):
                    logger.debug(
                        "Quota ETag conflict (attempt %d/%d) for %s — retrying",
                        attempt + 1, _MAX_RETRY, entity_id,
                    )
                    continue
                logger.exception(
                    "Unexpected quota write error for %s — allowing session", entity_id
                )
                return True

        logger.warning("Quota retries exhausted for %s — allowing session", entity_id)
        return True

    async def record_duration(
        self,
        entity_id: str,
        duration_seconds: float,
        date_str: Optional[str] = None,
    ) -> None:
        """Increment minutes_used by ceil(duration_seconds / 60)."""
        if not self.enabled or duration_seconds <= 0:
            return

        minutes_to_add = math.ceil(duration_seconds / 60)
        date_str = date_str or _today_utc()

        for attempt in range(_MAX_RETRY):
            entity = await self._get_quota_entity(entity_id, date_str)
            current = int(entity.get("minutes_used", 0))
            updated = dict(entity)
            updated["minutes_used"] = current + minutes_to_add

            try:
                table = self._table.get_table_client(_QUOTA_TABLE)
                etag = entity.get("etag") or entity.get("@odata.etag")
                if etag and current > 0:
                    await table.update_entity(
                        entity=updated,
                        match_condition=MatchConditions.IfNotModified,
                        etag=etag,
                    )
                else:
                    await table.upsert_entity(entity=updated)

                logger.info(
                    "Duration recorded entity=%s date=%s +%dm (total=%dm)",
                    entity_id, date_str, minutes_to_add, current + minutes_to_add,
                )
                return

            except Exception as exc:
                if "ConditionNotMet" in type(exc).__name__ or "ResourceModified" in str(exc):
                    logger.debug(
                        "Duration ETag conflict (attempt %d/%d) for %s — retrying",
                        attempt + 1, _MAX_RETRY, entity_id,
                    )
                    continue
                logger.exception(
                    "Unexpected duration write error for %s — skipping", entity_id
                )
                return

        logger.warning("Duration retries exhausted for %s — skipping", entity_id)
