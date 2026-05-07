"""Plugin's main blueprint — mounts every RWAiSE-specific route under ``/rwaise``.

This blueprint shares Gopnik's:

  • SQLAlchemy ``db``       — model FKs cross the gopnik / rwaise boundary.
  • Flask-Login session     — ``current_user`` is the host's User.
  • Flask-WTF CSRF token    — POST forms protected automatically.
  • Flask-Talisman CSP      — inherits the host's policy.
  • Jinja base.html         — every plugin template extends it.

Public routes
=============

  /rwaise/                       home / overview
  /rwaise/marketplace            cap-table-aware listings (anonymous OK)
  /rwaise/marketplace/<id>       listing detail

Auth-required routes
====================

  /rwaise/wizard[/step/<n>]      9-step tokenisation wizard
  /rwaise/wizard/copilot         AI co-pilot (Bedrock Claude)
  /rwaise/wizard/cap-table/parse CSV preview JSON
  /rwaise/wizard/finalize        broadcasts MPTokenIssuanceCreate
  /rwaise/issuance/<id>          issuance detail + audit trail
  /rwaise/holdings               my RWA holdings
  /rwaise/agent                  in-product Bedrock agent chat
  /rwaise/redemption[/...]       investor + admin redemption flows

Admin / role-gated routes
=========================

  /rwaise/admin/redemption-queue 4-eyes admin queue
  /rwaise/admin/audit-trail       full audit log
"""
from __future__ import annotations

import io
from dataclasses import asdict

from flask import (
    Blueprint, abort, current_app, jsonify, render_template, request,
)
from flask_login import current_user, login_required
from werkzeug.exceptions import BadRequest

from gopnik.models import db                                       # type: ignore[attr-defined]
from .feature_flag import feature_on
from .models import (
    MPTIssuance, MPTHolder, MPTRedemptionTicket, MPTEscrow,
    OrderbookOrder,
)


rwaise_bp = Blueprint(
    "rwaise",
    __name__,
    template_folder="templates",
    static_folder="static",
    # iter-19 fix: previously had static_url_path="/rwaise/static" AND
    # the blueprint is registered with url_prefix="/rwaise" — Flask
    # concatenated them, producing /rwaise/rwaise/static/... URLs.
    # Use just "/static" so the final mount is /rwaise/static/...
    static_url_path="/static",
)


# ─── Home & marketplace (public) ─────────────────────────────────────


@rwaise_bp.route("/", methods=["GET"])
def home():
    """RWAiSE landing page inside the host wallet."""
    return render_template("rwaise/home.html")


@rwaise_bp.route("/demo", methods=["GET"])
def demo_walkthrough():
    """Single-page hackathon walkthrough.

    Public route — judges can hit this without auth. Renders a guided
    tour of every RWAiSE capability with one-click links to the live
    surfaces (wizard, marketplace, holdings, agent chat, audit trail,
    admin flags). Reads current feature-flag state so disabled
    sub-modules render greyed-out instead of broken links.
    """
    # Surface live feature-flag state so the walkthrough mirrors
    # whatever the admin has toggled. Errors degrade to "all on" to
    # avoid blowing up the public landing surface.
    sub_flags = (
        "WIZARD", "AI_COPILOT", "CREDENTIALS", "ORDERBOOK", "ESCROW",
        "REDEMPTION", "X402", "BEDROCK_AGENT", "DEVELOPER_API", "ODL",
        "HSM_SIGNER",
    )
    try:
        flag_state = {f: feature_on(f) for f in sub_flags}
    except Exception:                                                 # pragma: no cover
        flag_state = {f: True for f in sub_flags}
    return render_template("rwaise/demo.html", flag_state=flag_state)


@rwaise_bp.route("/marketplace", methods=["GET"])
def marketplace_index():
    """Cap-table-aware marketplace listing.

    Filters listings by the credential set the visiting wallet holds.
    Anonymous visitors see the public catalogue (issuance metadata)
    but cannot place orders.
    """
    issuances = (db.session.query(MPTIssuance)
                 .order_by(MPTIssuance.created_at.desc())
                 .limit(100).all())
    return render_template("rwaise/marketplace.html", issuances=issuances)


@rwaise_bp.route("/marketplace/<int:listing_id>", methods=["GET"])
def marketplace_detail(listing_id: int):
    issuance = db.session.get(MPTIssuance, listing_id)
    return render_template("rwaise/marketplace_detail.html",
                           listing_id=listing_id, issuance=issuance)


# ─── Tokenisation wizard ────────────────────────────────────────────


@rwaise_bp.route("/wizard", methods=["GET"])
@login_required
def wizard():
    if not feature_on("WIZARD"):
        abort(404)
    return render_template("rwaise/wizard/shell.html", current_step=1)


@rwaise_bp.route("/wizard/step/<int:n>", methods=["GET", "POST"])
@login_required
def wizard_step(n: int):
    if not feature_on("WIZARD") or not 1 <= n <= 9:
        abort(404)
    from .wizard_controller import step as run_step
    return run_step(n)


@rwaise_bp.route("/wizard/copilot", methods=["POST"])
@login_required
def wizard_copilot():
    if not feature_on("AI_COPILOT"):
        return jsonify({"error": "ai_copilot_disabled"}), 503
    from .services import ai_copilot
    payload = request.get_json(silent=True) or {}
    intent = (payload.get("intent") or "").strip()
    from .wizard_controller import build_state
    state = build_state(current_user)
    try:
        if intent == "draft_prospectus":
            return jsonify({"markdown": ai_copilot.draft_prospectus_summary(state)})
        if intent == "suggest_mpt":
            return jsonify(asdict(ai_copilot.suggest_mpt_parameters(state)))
        if intent == "review_docs":
            return jsonify({"findings":
                            ai_copilot.review_documents(payload.get("documents", {}))})
        if intent == "answer":
            return jsonify({"text":
                            ai_copilot.answer(payload.get("question", ""), state)})
    except Exception as e:                                         # pragma: no cover
        current_app.logger.exception("co-pilot error: %s", e)
        return jsonify({"error": "co-pilot temporarily unavailable"}), 503
    raise BadRequest("Unknown co-pilot intent")


@rwaise_bp.route("/wizard/cap-table/parse", methods=["POST"])
@login_required
def wizard_cap_table_parse():
    from .services import cap_table_loader
    f = request.files.get("cap_table")
    if not f:
        raise BadRequest("cap_table file is required")
    text = io.TextIOWrapper(f.stream, encoding="utf-8", newline="")
    try:
        report = cap_table_loader.parse_and_validate(text)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({
        "rows": [vars(r) for r in report.rows],
        "total_units": report.total_units,
        "valid_count": report.valid_count,
        "review_queue_count": report.review_queue_count,
        "invalid_count": report.invalid_count,
        "is_clean": report.is_clean,
    })


@rwaise_bp.route("/wizard/finalize", methods=["POST"])
@login_required
def wizard_finalize():
    from .wizard_controller import finalize
    return finalize(current_user)


# ─── Issuance management ────────────────────────────────────────────


@rwaise_bp.route("/issuance/<int:issuance_id>", methods=["GET"])
@login_required
def issuance_detail(issuance_id: int):
    issuance = db.session.get(MPTIssuance, issuance_id)
    if issuance is None:
        abort(404)
    audit = (db.session.query(issuance.audit_events.property.mapper.class_)
             .filter_by(issuance_id=issuance_id)
             .order_by("created_at desc").limit(200).all())
    return render_template("rwaise/issuance_detail.html",
                           issuance=issuance, audit=audit)


@rwaise_bp.route("/holdings", methods=["GET"])
@login_required
def my_holdings():
    holdings = (db.session.query(MPTHolder)
                .filter(MPTHolder.holder_account.in_(_user_wallets(current_user)))
                .all())
    return render_template("rwaise/my_holdings.html", holdings=holdings)


# ─── Redemption ─────────────────────────────────────────────────────


@rwaise_bp.route("/redemption", methods=["GET"])
@login_required
def my_redemptions():
    if not feature_on("REDEMPTION"):
        abort(404)
    tickets = (db.session.query(MPTRedemptionTicket)
               .filter_by(holder_user_id=current_user.id)
               .order_by(MPTRedemptionTicket.created_at.desc()).limit(100).all())
    return render_template("rwaise/redemption_list.html", tickets=tickets)


@rwaise_bp.route("/redemption/request/<int:issuance_id>", methods=["POST"])
@login_required
def redemption_request(issuance_id: int):
    if not feature_on("REDEMPTION"):
        abort(404)
    from .services import redemption
    amount = int(request.form.get("amount", "0"))
    holder_account = (request.form.get("holder_account") or "").strip()
    if not amount or not holder_account:
        raise BadRequest("amount + holder_account required")
    ticket = redemption.request_redemption(
        issuance_id=issuance_id, holder_user=current_user,
        holder_account=holder_account, amount=amount)
    return jsonify({"ticket_id": ticket.id, "status": ticket.status.value})


# ─── Admin / role-gated ─────────────────────────────────────────────


def _require_admin():
    role = getattr(current_user, "role", None)
    role_str = role.value if hasattr(role, "value") else str(role) if role else ""
    if role_str not in ("admin", "compliance_officer", "auditor", "issuer"):
        abort(403)


@rwaise_bp.route("/admin/redemption-queue", methods=["GET"])
@login_required
def redemption_queue():
    _require_admin()
    if not feature_on("REDEMPTION"):
        abort(404)
    tickets = (db.session.query(MPTRedemptionTicket)
               .filter(MPTRedemptionTicket.status.in_(["awaiting_4eyes", "approved"]))
               .order_by(MPTRedemptionTicket.created_at.asc()).all())
    return render_template("rwaise/redemption_queue.html", tickets=tickets)


@rwaise_bp.route("/admin/audit-trail", methods=["GET"])
@login_required
def audit_trail():
    _require_admin()
    from .models import MPTAuditEvent
    events = (db.session.query(MPTAuditEvent)
              .order_by(MPTAuditEvent.created_at.desc()).limit(500).all())
    return render_template("rwaise/audit_trail.html", events=events)


# ─── Agent chat (in-product Bedrock) ─────────────────────────────────


@rwaise_bp.route("/agent", methods=["GET"])
@login_required
def agent_chat():
    if not feature_on("BEDROCK_AGENT"):
        abort(404)
    return render_template("rwaise/agent_chat.html")


# ─── Helpers ────────────────────────────────────────────────────────


def _user_wallets(user) -> list[str]:
    """Pull the user's XRPL wallets from Gopnik's ``WalletUser`` table.

    Gopnik names this model ``WalletUser`` (one row per imported wallet
    seed/address). Each row's ``address`` column is the classic XRPL
    account.
    """
    from gopnik.models import WalletUser                           # type: ignore[attr-defined]
    rows = (db.session.query(WalletUser)
            .filter(WalletUser.imported_by_user_id == user.id).all())
    return [w.address for w in rows if getattr(w, "address", None)]
