"""Plugin loader for RWAiSE — registers blueprints, models, nav, and CLI.

Called once from ``gopnik.create_app`` (see the one-line patch in
``patches/gopnik_init_py.diff``)::

    from .rwaise import register_plugin as register_rwaise
    register_rwaise(app)

The function is a no-op when ``RWAISE_ENABLED`` is falsy, so the call
is always safe — operators can ship this code to production and
toggle the plugin on per environment without redeploying.

Order of operations is important; if anything fails we log and continue
so a partially-broken plugin can never bring down the host wallet.
"""
from __future__ import annotations

import logging
from typing import Any

from flask import Flask

from .feature_flag import is_enabled, feature_on, all_flags


log = logging.getLogger(__name__)


def register_plugin(app: Flask) -> None:
    """Wire RWAiSE into the Gopnik app.

    Steps
    -----
    1. Master switch — return immediately when off.
    2. Models — import so SQLAlchemy registers them on the shared ``db``.
    3. Config — push every resolved flag into ``app.config`` so it's
       available in templates and other plugins.
    4. Blueprints — register conditionally, each gated by its sub-flag.
    5. Navigation — inject the "RWAiSE" tab into Gopnik's top nav via a
       Jinja context processor.
    6. Celery tasks — import so the @celery.task decorators run.
    7. Admin panel hooks — append RWAiSE-specific links to the admin
       sidebar.
    8. CLI commands — register ``flask rwaise *`` group on Gopnik's CLI.
    9. Logging — announce we're up.
    """
    if not is_enabled():
        log.info("RWAiSE plugin disabled (RWAISE_ENABLED is off).")
        return

    # 2. Models — must be imported so they attach to the shared db.
    try:
        from . import models  # noqa: F401
    except Exception as exc:                                       # pragma: no cover
        log.exception("RWAiSE plugin: failed to import models — aborting load: %s", exc)
        return

    # 3. Config — every resolved flag becomes a key in app.config so the
    # value flows through to templates and to other plugins.
    for k, v in all_flags().items():
        app.config.setdefault(k, v)

    # Plugin-level defaults that downstream services read from app.config.
    app.config.setdefault("RWAISE_PLATFORM_ADDRESS",
                          app.config.get("RWAISE_PLATFORM_ADDRESS", ""))
    app.config.setdefault("RWAISE_FEE_RECEIVER",
                          app.config.get("RWAISE_FEE_RECEIVER", ""))
    app.config.setdefault("RWAISE_BEDROCK_MODEL_ID",
                          "anthropic.claude-sonnet-4-6")

    # 4. Blueprints — each gated by its own sub-flag.
    _register_blueprints(app)

    # 5. Navigation hook.
    from .nav import inject_nav_context, register_nav_links
    app.context_processor(inject_nav_context)
    register_nav_links(app)

    # 6. Celery tasks — load lazily.
    try:
        from . import tasks  # noqa: F401
    except Exception as exc:                                       # pragma: no cover
        log.warning("RWAiSE plugin: tasks not loaded (Celery missing?): %s", exc)

    # 7. Admin panel hooks.
    try:
        from .admin.hooks import register_admin_hooks
        register_admin_hooks(app)
    except Exception as exc:                                       # pragma: no cover
        log.warning("RWAiSE plugin: admin hooks not registered: %s", exc)

    # 8. CLI.
    try:
        from .cli import rwaise_cli
        app.cli.add_command(rwaise_cli)
    except Exception as exc:                                       # pragma: no cover
        log.warning("RWAiSE plugin: CLI not registered: %s", exc)

    log.info("RWAiSE plugin loaded · flags=%s", all_flags())
    app.config["RWAISE_PLUGIN_LOADED"] = True


# ─── Internal: blueprint registration table ──────────────────────────


_BLUEPRINTS: list[tuple[str, str, str, str | None]] = [
    # (sub-flag-or-empty, import_path, attr, url_prefix)
    # The main rwaise blueprint is unconditional — its sub-routes
    # individually check sub-flags (so a user-mode admin can still
    # access /rwaise/admin/feature-flags even when WIZARD is off).
    ("",               "gopnik.rwaise.routes",                       "rwaise_bp",       "/rwaise"),
    ("",               "gopnik.rwaise.admin.feature_flags",          "feature_flags_bp", None),
    ("",               "gopnik.rwaise.admin.agent_caps",             "agent_caps_bp",   None),
    ("DEVELOPER_API",  "gopnik.rwaise.api.oauth",                    "oauth_bp",        None),
    ("DEVELOPER_API",  "gopnik.rwaise.api.public",                   "public_api_bp",   None),
    ("DEVELOPER_API",  "gopnik.rwaise.api.developer_portal",         "developer_bp",    None),
    # iter-18 — RWAiSE Trader (Bedrock Claude + CoinDesk + x402 + Gopnik wallet).
    ("BEDROCK_AGENT",  "gopnik.rwaise.agents.chat_routes",           "agent_chat_bp",   None),
]


def _register_blueprints(app: Flask) -> None:
    """Register every blueprint in ``_BLUEPRINTS`` whose sub-flag is on."""
    import importlib

    for sub_flag, import_path, attr, prefix in _BLUEPRINTS:
        if sub_flag and not feature_on(sub_flag):
            log.debug("RWAiSE: blueprint %s skipped (flag %s off)",
                      attr, sub_flag)
            continue
        try:
            module = importlib.import_module(import_path)
            bp = getattr(module, attr)
            if prefix is not None:
                app.register_blueprint(bp, url_prefix=prefix)
            else:
                app.register_blueprint(bp)
            log.debug("RWAiSE: registered blueprint %s", attr)
        except Exception as exc:                                   # pragma: no cover
            log.exception("RWAiSE: failed to register %s: %s", import_path, exc)
