"""Inject the RWAiSE tab into Gopnik's top navigation bar.

Strategy
========

We avoid editing every Gopnik template. Instead we:

1. Provide a Jinja **context processor** that exposes
   ``rwaise_nav_links`` (a list of dicts) and ``rwaise_plugin_enabled``.
2. Provide a small Jinja fragment ``templates/_nav_tab.html`` that the
   host's ``base.html`` includes once.

Gopnik's ``base.html`` only needs **one new line** in its <nav> block::

    {% include 'rwaise/_nav_tab.html' %}

The fragment self-checks ``rwaise_plugin_enabled`` and renders nothing
when the plugin is off. The exact patch is in
``patches/gopnik_base_html.diff``.

Sub-tabs (jurisdiction-by-flag)
-------------------------------

If a user is an issuer or admin, the "RWAiSE" tab expands into a
dropdown with quick links. We populate the dropdown server-side so
search engines see the same DOM as the user.
"""
from __future__ import annotations

from typing import Any, List, TypedDict

from flask import url_for
from flask_login import current_user

from .feature_flag import is_enabled, feature_on


class NavLink(TypedDict):
    label: str
    href: str
    badge: str | None
    visible_to_anon: bool


def _link(label: str, endpoint: str, *, badge: str | None = None,
          visible_to_anon: bool = False) -> NavLink:
    try:
        href = url_for(endpoint)
    except Exception:                                              # pragma: no cover
        href = "#"
    return {
        "label": label,
        "href": href,
        "badge": badge,
        "visible_to_anon": visible_to_anon,
    }


def inject_nav_context() -> dict[str, Any]:
    """Jinja context processor — runs on every request."""
    if not is_enabled():
        return {
            "rwaise_plugin_enabled": False,
            "rwaise_nav_links": [],
            "rwaise_nav_root": None,
        }

    links: List[NavLink] = []

    # Hackathon walkthrough — top of the dropdown so demo judges can
    # find the guided tour the moment they hit the wallet.
    links.append(_link("Demo walkthrough", "rwaise.demo_walkthrough",
                       badge="DEMO", visible_to_anon=True))

    # Marketplace is visible to anonymous visitors as a discovery surface.
    links.append(_link("Marketplace", "rwaise.marketplace_index",
                       visible_to_anon=True))

    # Auth-required links.
    if getattr(current_user, "is_authenticated", False):
        if feature_on("WIZARD"):
            links.append(_link("Tokenize an asset", "rwaise.wizard"))
        links.append(_link("My RWA holdings", "rwaise.my_holdings"))
        if feature_on("BEDROCK_AGENT"):
            links.append(_link("RWAiSE Trader (AI)",
                               "rwaise.agent_chat", badge="AI"))

        role = getattr(current_user, "role", None)
        # Issuer / admin extras.
        role_str = role.value if hasattr(role, "value") else str(role) if role else ""
        if role_str in ("issuer", "compliance_officer", "admin"):
            if feature_on("REDEMPTION"):
                links.append(_link("Redemption queue",
                                   "rwaise.redemption_queue", badge="4-eyes"))
            if feature_on("DEVELOPER_API"):
                links.append(_link("Developer portal", "developer.index"))
            links.append(_link("Issuance audit trail", "rwaise.audit_trail"))

        # Plugin master switch — visible only to admins.
        if role_str == "admin":
            links.append(_link("Plugin feature flags",
                               "rwaise_admin_flags.index", badge="ADMIN"))

    return {
        "rwaise_plugin_enabled": True,
        "rwaise_nav_links": links,
        "rwaise_nav_root": _link("RWAiSE", "rwaise.home", badge="NEW",
                                  visible_to_anon=True),
    }


def register_nav_links(app) -> None:
    """Optional: expose helpers to other Jinja extensions."""
    @app.template_filter("rwaise_active_if")
    def _rwaise_active_if(href: str, current_path: str) -> str:
        return "is-active" if current_path.startswith(href) else ""
