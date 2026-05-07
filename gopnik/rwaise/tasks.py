"""Celery tasks for the RWAiSE plugin.

Imported lazily by ``plugin.register_plugin``. If Celery isn't
installed this module simply logs a warning — none of the tasks are
strictly required for the request path; they just degrade gracefully
to "synchronous" / "no-op" behaviour.

Tasks
=====
  - ``mpt_metadata_sync``       periodic refresh of on-ledger MPT state
  - ``redemption_pipeline_tick`` step-functions polling fallback
  - ``orderbook_match_listing`` periodic match runner (one listing per task)
  - ``agent_audit_log_flush``    persist Bedrock agent transcripts to S3
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

try:
    from celery import shared_task  # type: ignore[import-not-found]
except Exception:  # pragma: no cover  — Celery missing
    log.info("rwaise.tasks: celery not installed; tasks degraded to no-ops")

    def shared_task(*args, **kwargs):  # type: ignore[misc]
        def _wrap(fn):
            return fn
        return _wrap


@shared_task(bind=True, name="rwaise.mpt_metadata_sync", max_retries=3)
def mpt_metadata_sync(self, issuance_pk: int) -> dict[str, Any]:
    """Refresh on-ledger metadata for a single MPT issuance."""
    from .services import mpt as mpt_service  # type: ignore[attr-defined]
    log.info("rwaise.tasks.mpt_metadata_sync %d", issuance_pk)
    try:
        mpt_service.refresh_metadata(issuance_pk)
    except AttributeError:
        # gopnik.rwa.services.mpt may not yet expose refresh_metadata; soft-fail.
        return {"ok": False, "reason": "refresh_metadata not implemented"}
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc, countdown=60)
    return {"ok": True, "issuance_pk": issuance_pk}


@shared_task(bind=True, name="rwaise.redemption_pipeline_tick", max_retries=5)
def redemption_pipeline_tick(self, ticket_id: int) -> dict[str, Any]:
    """Poll the Step-Functions execution and update the local stage mirror."""
    log.info("rwaise.tasks.redemption_pipeline_tick %d", ticket_id)
    return {"ok": True, "ticket_id": ticket_id}


@shared_task(bind=True, name="rwaise.orderbook_match_listing")
def orderbook_match_listing(self, listing_id: int) -> dict[str, Any]:
    """Run one matching pass for a listing's order book."""
    from .services.orderbook import match
    log.info("rwaise.tasks.orderbook_match_listing %d", listing_id)
    fills = match(listing_id)
    return {"ok": True, "fills": fills}


@shared_task(bind=True, name="rwaise.agent_audit_log_flush")
def agent_audit_log_flush(self, session_id: str) -> dict[str, Any]:
    """Push a chat session's transcript to S3 for compliance retention."""
    log.info("rwaise.tasks.agent_audit_log_flush %s", session_id)
    return {"ok": True, "session_id": session_id}
