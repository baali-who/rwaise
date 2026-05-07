"""Developer portal — register apps, view usage, rotate secrets.

Mounted at /developer/. Auth-required (Gopnik's flask_login).
"""
from __future__ import annotations

import logging
import secrets

from flask import (
    Blueprint, abort, flash, jsonify, redirect, render_template,
    request, url_for,
)
from flask_login import current_user, login_required

log = logging.getLogger(__name__)
developer_bp = Blueprint(
    "developer", __name__, url_prefix="/developer",
    template_folder="../../templates",
)


@developer_bp.route("/", methods=["GET"])
@login_required
def index():
    from gopnik.rwaise.models import DeveloperClient
    from gopnik.models import db  # type: ignore[attr-defined]
    clients = (db.session.query(DeveloperClient)
               .filter_by(owner_user_id=current_user.id)
               .order_by(DeveloperClient.created_at.desc()).all())
    return render_template("rwaise/developer_index.html", clients=clients)


@developer_bp.route("/clients/new", methods=["GET", "POST"])
@login_required
def new_client():
    from gopnik.rwaise.models import (
        DeveloperClient, DeveloperClientStatus,
    )
    from gopnik.rwaise.api.oauth import hash_secret
    from gopnik.models import db  # type: ignore[attr-defined]

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Name is required.", "warning")
            return redirect(url_for("developer.new_client"))
        plain_secret = secrets.token_urlsafe(32)
        client_id = "rwaise_" + secrets.token_urlsafe(16)
        scopes_raw = (request.form.get("scopes") or "read").strip()
        scopes = sorted({s.strip() for s in scopes_raw.split() if s.strip()})
        c = DeveloperClient(
            owner_user_id=current_user.id,
            name=name[:120],
            description=(request.form.get("description") or "").strip() or None,
            homepage_url=(request.form.get("homepage_url") or "").strip() or None,
            client_id=client_id,
            client_secret_hash=hash_secret(plain_secret),
            scopes=scopes,
            status=DeveloperClientStatus.active,  # auto-approve in demo; manual in prod
        )
        db.session.add(c)
        db.session.commit()
        # One-shot reveal of the plain secret — render once, never again.
        return render_template(
            "rwaise/developer_client_created.html",
            client=c, plain_secret=plain_secret,
        )
    return render_template("rwaise/developer_new_client.html")


@developer_bp.route("/clients/<int:client_id>/usage", methods=["GET"])
@login_required
def client_usage(client_id: int):
    from gopnik.rwaise.models import DeveloperClient, DeveloperAPICallLog
    from gopnik.models import db  # type: ignore[attr-defined]
    client = db.session.get(DeveloperClient, client_id)
    if client is None or client.owner_user_id != current_user.id:
        abort(404)
    rows = (db.session.query(DeveloperAPICallLog)
            .filter_by(client_id=client.id)
            .order_by(DeveloperAPICallLog.created_at.desc())
            .limit(200).all())
    return render_template("rwaise/developer_client_usage.html",
                           client=client, calls=rows)
