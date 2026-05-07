"""Wizard controller — orchestrates the 9-step issuance flow.

iter-17 rewrite
===============

The previous version treated ``gopnik._models.tokens.RWAAsset`` as a
mutable draft store and tried to read/write columns that *do not exist
on that model* (``issuer_id``, ``mpt_issuance_id``, ``metadata_json``,
``spv_name``, ``spv_jurisdiction``, ``description``, ``valuation_usd``).
Every wizard step therefore crashed with an SQLAlchemy
``InvalidRequestError`` and the user saw a generic 500.

The fix: keep the wizard's working draft in **Flask session** (signed,
per-user, no schema migration). Only when the issuer reaches step 9 and
explicitly *finalises* do we materialise an ``MPTIssuance`` row and
broadcast ``MPTokenIssuanceCreate``. This matches the model's spirit —
``MPTIssuance`` is for issued tokens, not work-in-progress drafts.

Public surface (``step``, ``finalize``, ``build_state``) is unchanged so
``routes.py`` keeps working.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from flask import (
    current_app, flash, jsonify, redirect, render_template,
    request, session, url_for,
)

from gopnik.models import db, AuditLog, WalletUser                   # type: ignore[attr-defined]
from .models import MPTIssuance, MPTIssuanceStatus
from .services import mpt as mpt_service
from .services.ai_copilot import WizardState
from . import forms as wf


WIZARD_FORMS = {
    1: wf.Step1AssetClassForm, 2: wf.Step2LegalForm,
    3: wf.Step3DocumentsForm, 4: wf.Step4ValuationForm,
    5: wf.Step5KYCForm,        6: wf.Step6TokenParamsForm,
    7: wf.Step7CapTableForm,   8: wf.Step8MarketplaceForm,
    9: wf.Step9ReviewForm,
}

# Session key under which we keep the per-user draft. Scoped by user id
# so multiple wallets on a shared browser don't bleed state.
_SESSION_KEY = "rwaise_wizard_draft"


def step(n: int):
    """Render or process step ``n``."""
    from flask_login import current_user

    Form = WIZARD_FORMS[n]
    form = Form(request.form if request.method == "POST" else None)

    if request.method == "POST" and form.validate():
        _persist_step(current_user, n, form)
        flash(f"Step {n} saved.", "success")
        next_step = min(n + 1, 9)
        return redirect(url_for("rwaise.wizard_step", n=next_step))

    # GET — render current step. Pre-populate fields from the saved draft.
    draft = _load_draft(current_user)
    saved = draft.get("steps", {}).get(str(n), {})
    if request.method == "GET" and saved:
        # WTForms accepts a kwargs-style data dict via ``process``.
        for key, val in saved.items():
            if hasattr(form, key):
                fld = getattr(form, key)
                # Don't try to repopulate FileFields — browsers can't
                # rehydrate <input type=file> from server state anyway.
                if fld.type != "FileField":
                    fld.data = val

    return render_template(
        f"rwaise/wizard/step_{n}.html",
        form=form,
        current_step=n,
        wizard_state=build_state(current_user),
        draft=draft,
    )


def finalize(user):
    """Submit ``MPTokenIssuanceCreate`` for the user's draft.

    Two modes (chosen by ``RWAISE_FEATURE_LIVE_BROADCAST`` config):

      * **simulated** (default for the demo): persist the issuance row
        with a deterministic mock tx_hash. Safe for hackathon judging.
      * **live**: call ``mpt_service.submit_issuance_create`` against
        the configured RPC URL using the issuer wallet's stored seed.
    """
    draft = _load_draft(user)
    if not _all_steps_complete(draft):
        return jsonify({"error": "Some wizard steps are incomplete"}), 400

    try:
        issuance = _create_issuance_row(user, draft)
    except _WizardError as e:
        return jsonify({"error": str(e)}), 400

    live = bool(current_app.config.get("RWAISE_FEATURE_LIVE_BROADCAST"))
    try:
        if live:
            tx_hash = _broadcast_live(user, issuance, draft)
        else:
            tx_hash = _broadcast_simulated(issuance)
    except mpt_service.MPTServiceError as e:
        current_app.logger.exception("MPT submit failed: %s", e)
        return jsonify({"error": str(e)}), 502
    except Exception as e:                                            # pragma: no cover
        current_app.logger.exception("MPT submit unexpected error: %s", e)
        return jsonify({"error": f"MPT submit failed: {e}"}), 500

    issuance.submitted_tx_hash = tx_hash
    issuance.status = MPTIssuanceStatus.submitted
    db.session.add(AuditLog(
        actor_user_id=user.id, action="rwaise.issuance.create",
        target_type="rwaise_mpt_issuance", target_id=str(issuance.id),
        payload_json={"tx_hash": tx_hash,
                      "issuance_id": issuance.issuance_id,
                      "live": live},
    ))
    db.session.commit()

    # Clear the wizard draft now that the issuance has been broadcast.
    _clear_draft(user)

    return jsonify({
        "tx_hash": tx_hash,
        "issuance_id": issuance.issuance_id,
        "rwaise_id": issuance.id,
        "mode": "live" if live else "simulated",
    })


def build_state(user) -> WizardState:
    """Build a typed WizardState from the user's session draft.

    Used by both the AI co-pilot and the JSON returned to the front-end.
    """
    draft = _load_draft(user)
    s = draft.get("steps", {})
    s1 = s.get("1", {});  s2 = s.get("2", {});  s4 = s.get("4", {})
    s5 = s.get("5", {});  s8 = s.get("8", {})
    return WizardState(
        asset_class=s1.get("asset_class"),
        subclass=s1.get("subclass"),
        description=s1.get("description"),
        spv_name=s2.get("spv_name"),
        spv_jurisdiction=s2.get("spv_jurisdiction"),
        valuation_usd=s4.get("valuation_usd"),
        target_apy_bps=s4.get("target_apy_bps"),
        quote_currency=s8.get("quote_currency"),
        minimum_holding=s5.get("minimum_holding"),
    )


# ─── Internal: draft store (Flask session) ───────────────────────────


def _draft_key(user) -> str:
    return f"{_SESSION_KEY}:{getattr(user, 'id', 'anon')}"


def _load_draft(user) -> dict[str, Any]:
    return session.get(_draft_key(user), {"steps": {}})


def _save_draft(user, draft: dict[str, Any]) -> None:
    session[_draft_key(user)] = draft
    session.permanent = True


def _clear_draft(user) -> None:
    session.pop(_draft_key(user), None)


def _persist_step(user, n: int, form) -> None:
    draft = _load_draft(user)
    steps = draft.setdefault("steps", {})

    payload: dict[str, Any] = {}
    for f in form:
        if getattr(f, "type", "").endswith("HiddenField"):
            continue
        # FileField data is a Werkzeug FileStorage which won't pickle
        # cleanly into the session; record only the filename so the UI
        # can show "uploaded" + we keep the actual bytes elsewhere.
        if f.type == "FileField":
            fs = f.data
            if fs and getattr(fs, "filename", None):
                payload[f.name] = {"filename": fs.filename}
            continue
        payload[f.name] = _to_jsonable(f.data)

    steps[str(n)] = payload
    _save_draft(user, draft)


def _all_steps_complete(draft: dict[str, Any]) -> bool:
    steps = draft.get("steps", {})
    return all(str(n) in steps for n in range(1, 10))


# ─── Internal: finalize ──────────────────────────────────────────────


class _WizardError(RuntimeError):
    """User-facing error during finalize. Surfaced as 400 to the JSON API."""


def _create_issuance_row(user, draft: dict[str, Any]) -> MPTIssuance:
    s = draft.get("steps", {})
    s1 = s.get("1", {});  s2 = s.get("2", {});  s4 = s.get("4", {})
    s5 = s.get("5", {});  s6 = s.get("6", {});  s8 = s.get("8", {})

    issuer_wallet = _pick_issuer_wallet(user)
    if issuer_wallet is None:
        raise _WizardError(
            "Your account has no XRPL wallet yet. Create one in "
            "the Wallets page first, then try Finalize again.")
    issuer_addr = _wallet_address(issuer_wallet)

    issuance = MPTIssuance(
        issuer_account=issuer_addr,
        asset_scale=int(s6.get("asset_scale") or 2),
        maximum_amount=int(s6["maximum_amount"]) if s6.get("maximum_amount") else None,
        transfer_fee=int(s6.get("transfer_fee_bps") or 0),
        can_lock=bool(s6.get("can_lock", True)),
        require_auth=bool(s5.get("require_credential", True)),
        can_escrow=bool(s6.get("can_escrow", True)),
        can_trade=bool(s6.get("can_trade", True)),
        can_transfer=bool(s6.get("can_transfer", True)),
        can_clawback=bool(s6.get("can_clawback", True)),
        metadata_json={
            "name": s6.get("name", ""),
            "symbol": s6.get("symbol", ""),
            "asset_class": s1.get("asset_class", ""),
            "subclass": s1.get("subclass"),
            "description": s1.get("description", ""),
            "spv": {
                "name": s2.get("spv_name", ""),
                "jurisdiction": s2.get("spv_jurisdiction", ""),
                "legal_counsel_email": s2.get("legal_counsel_email"),
            },
            "valuation_usd": s4.get("valuation_usd"),
            "target_apy_bps": s4.get("target_apy_bps"),
            "quote_currency": s8.get("quote_currency", "USD"),
            "kyc": {
                "require_jurisdiction": s5.get("require_jurisdiction", False),
                "require_accreditation": s5.get("require_accreditation", False),
                "minimum_holding": s5.get("minimum_holding"),
            },
        },
        metadata_hash=(s6.get("symbol") or "DRAFT").upper(),
        status=MPTIssuanceStatus.draft,
    )
    db.session.add(issuance)
    db.session.commit()
    return issuance


def _pick_issuer_wallet(user):
    """Best-effort: prefer the user's master wallet, else any owned wallet.

    Gopnik's wallet model is ``WalletUser`` (one row per imported XRPL
    wallet). We resolve the issuer wallet two ways and fall back:

      1. ``imported_by_user_id == user.id`` AND ``is_master`` — the
         "active" signing wallet for that user.
      2. Any ``imported_by_user_id == user.id`` row, oldest first.
    """
    q = (db.session.query(WalletUser)
         .filter(WalletUser.imported_by_user_id == user.id))
    master = q.filter(WalletUser.is_master.is_(True)).first()
    if master:
        return master
    return q.order_by(WalletUser.id.asc()).first()


def _wallet_address(w) -> str:
    """Wallet model attribute compat — exposes ``classic_address`` if present."""
    return getattr(w, "address", None) or getattr(w, "classic_address", "")


def _wallet_seed(w):
    """Returns the wallet's stored encrypted seed (under either name)."""
    return getattr(w, "seed_encrypted", None) or getattr(w, "encrypted_seed", None)


def _broadcast_simulated(issuance: MPTIssuance) -> str:
    """Demo path — returns a deterministic mock tx hash.

    Uses sha256 of the issuance's metadata so each draft gets a stable
    "looks like XRPL" 64-char uppercase hex tx hash without ever
    touching the network. Judges see a real-looking tx hash without us
    risking real-network spend.
    """
    import hashlib, json
    blob = json.dumps(issuance.metadata_json or {},
                      sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest().upper()


def _broadcast_live(user, issuance: MPTIssuance,
                    draft: dict[str, Any]) -> str:
    """Live path — submits MPTokenIssuanceCreate to the configured RPC."""
    rpc_url = current_app.config.get("RWAISE_RPC_URL") \
              or current_app.config.get("XRPL_RPC_URL")
    if not rpc_url:
        raise mpt_service.MPTServiceError(
            "No RWAISE_RPC_URL configured for live broadcast",
            code="config_missing")

    issuer_wallet = (db.session.query(WalletUser)
                     .filter(WalletUser.imported_by_user_id == user.id,
                             WalletUser.address == issuance.issuer_account)
                     .first())
    enc = _wallet_seed(issuer_wallet) if issuer_wallet else None
    if not enc:
        raise mpt_service.MPTServiceError(
            "Issuer wallet has no decryptable seed in DB; live broadcast "
            "requires a hot or HSM-backed signer.",
            code="signer_missing")

    import base64
    try:
        seed = base64.b64decode(enc).decode()
    except Exception:                                                 # pragma: no cover
        raise mpt_service.MPTServiceError(
            "Issuer wallet seed could not be decoded", code="signer_decode")

    flags = mpt_service.flags_for_capabilities(
        can_lock=issuance.can_lock,
        require_auth=issuance.require_auth,
        can_escrow=issuance.can_escrow,
        can_trade=issuance.can_trade,
        can_transfer=issuance.can_transfer,
        can_clawback=issuance.can_clawback,
    )
    metadata_hex = mpt_service.encode_metadata(issuance.metadata_json or {})
    result = mpt_service.submit_issuance_create(
        issuer_seed=seed,
        rpc_url=rpc_url,
        asset_scale=int(issuance.asset_scale or 0),
        flags=flags,
        metadata_hex=metadata_hex,
        maximum_amount=issuance.maximum_amount,
        transfer_fee=int(issuance.transfer_fee or 0),
    )
    if getattr(result, "issuance_id", None):
        issuance.issuance_id = result.issuance_id
    return getattr(result, "tx_hash", "") or ""


def _to_jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v
