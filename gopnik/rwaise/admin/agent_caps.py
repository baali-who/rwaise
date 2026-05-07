"""Admin UI: per-user agent caps.

The Bedrock agent enforces per-user spending caps; admins set them
here. Mounted at /admin/agent-caps/.
"""
from __future__ import annotations

from flask import (
    Blueprint, abort, flash, redirect, render_template, request, url_for,
)
from flask_login import current_user, login_required

agent_caps_bp = Blueprint(
    "agent_caps", __name__,
    url_prefix="/admin/agent-caps",
    template_folder="../templates",
)


def _require_admin():
    if not getattr(current_user, "is_admin", False):
        abort(403)


@agent_caps_bp.route("/", methods=["GET"])
@login_required
def index():
    _require_admin()
    from ..models import AgentCap
    from gopnik.models import db  # type: ignore[attr-defined]
    rows = db.session.query(AgentCap).order_by(AgentCap.user_id).all()
    return render_template("rwaise/admin/agent_caps.html", rows=rows)


@agent_caps_bp.route("/<int:user_id>", methods=["POST"])
@login_required
def update(user_id: int):
    _require_admin()
    from ..models import AgentCap
    from gopnik.models import db  # type: ignore[attr-defined]
    row = (db.session.query(AgentCap)
           .filter_by(user_id=user_id).first())
    if row is None:
        row = AgentCap(user_id=user_id)
        db.session.add(row)
    row.daily_buy_cap_usd = int(request.form.get("daily_buy_cap_usd") or 0)
    row.daily_sell_cap_usd = int(request.form.get("daily_sell_cap_usd") or 0)
    row.transfer_cap_drops = int(request.form.get("transfer_cap_drops") or 0)
    row.require_2fa_above_drops = int(request.form.get("require_2fa_above_drops") or 0)
    row.enabled = (request.form.get("enabled") or "0").lower() in ("1", "true", "yes", "on")
    row.last_updated_by = getattr(current_user, "id", None)
    db.session.commit()
    flash(f"Caps updated for user {user_id}", "success")
    return redirect(url_for("agent_caps.index"))
