"""Admin UI for toggling RWAiSE feature flags.

THE main demo lever: an admin signs in, navigates to /rwaise/admin/feature-flags,
flips the master switch off → the entire RWAiSE tab disappears for users
within 5s (local cache TTL).

Mounted at /rwaise/admin/feature-flags.
"""
from __future__ import annotations

import logging

from flask import (
    Blueprint, abort, flash, jsonify, redirect, render_template,
    request, url_for,
)
from flask_login import current_user, login_required

from ..feature_flag import SUB_FLAGS, all_flags, set_feature

log = logging.getLogger(__name__)
feature_flags_bp = Blueprint(
    "rwaise_admin_flags", __name__,
    url_prefix="/rwaise/admin/feature-flags",
    template_folder="../templates",
)


def _require_admin():
    role = getattr(current_user, "role", None)
    role_str = role.value if hasattr(role, "value") else str(role) if role else ""
    if role_str != "admin" and not getattr(current_user, "is_admin", False):
        abort(403)


@feature_flags_bp.route("/", methods=["GET"])
@login_required
def index():
    _require_admin()
    flags = all_flags()
    return render_template(
        "rwaise/admin/feature_flags.html",
        flags=flags,
        sub_flags=SUB_FLAGS,
    )


@feature_flags_bp.route("/toggle", methods=["POST"])
@login_required
def toggle():
    _require_admin()
    name = (request.form.get("name") or "").strip()
    enabled = (request.form.get("enabled") or "").strip().lower() in ("1", "true", "on", "yes")
    reason = (request.form.get("reason") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    new_value = set_feature(
        name, enabled,
        actor_user_id=getattr(current_user, "id", None),
        reason=reason,
    )
    log.info("rwaise.admin.flags %s set %s=%s",
             getattr(current_user, "id", None), name, new_value)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"name": name, "enabled": new_value})
    flash(f"Set {name} = {new_value}", "success")
    return redirect(url_for("rwaise_admin_flags.index"))


@feature_flags_bp.route("/audit", methods=["GET"])
@login_required
def audit():
    _require_admin()
    from ..models import FeatureFlagAudit
    from gopnik.models import db  # type: ignore[attr-defined]
    rows = (db.session.query(FeatureFlagAudit)
            .order_by(FeatureFlagAudit.created_at.desc()).limit(200).all())
    return render_template("rwaise/admin/feature_flags_audit.html", rows=rows)
