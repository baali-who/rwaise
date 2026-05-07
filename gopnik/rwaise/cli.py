"""``flask rwaise *`` CLI commands.

Registered onto Gopnik's existing CLI by ``plugin.register_plugin``.

Commands::

    flask rwaise status              show resolved feature flags
    flask rwaise bootstrap-credentials  create RWAISE-VINV-* MPT issuances
    flask rwaise seed-demo           seed 8 realistic 2030 RWA assets
    flask rwaise seed-reset          drop demo data
"""
from __future__ import annotations

import json

import click
from flask.cli import AppGroup

from .feature_flag import all_flags, is_enabled


rwaise_cli = AppGroup("rwaise", help="RWAiSE plugin admin commands.")


@rwaise_cli.command("status")
def status():
    """Print the resolved feature flags as JSON."""
    click.echo(json.dumps(all_flags(), indent=2))


@rwaise_cli.command("bootstrap-credentials")
def bootstrap_credentials():
    """Idempotently create the platform's XLS-65 credential MPTs."""
    if not is_enabled():
        click.echo("RWAiSE is disabled (RWAISE_ENABLED=0). Aborting.", err=True)
        raise SystemExit(1)
    from .services.credential_issuer import bootstrap_catalogue
    rows = bootstrap_catalogue()
    click.echo(f"Bootstrapped {len(rows)} credential types.")


@rwaise_cli.command("seed-demo")
@click.option("--investors", default=10, show_default=True, type=int)
@click.option("--with-orderbook/--no-orderbook", default=True)
def seed_demo(investors: int, with_orderbook: bool):
    """Seed 8 realistic-by-2030 RWA issuances (US T-Bill, EU bond,
    NYC office, trade-finance, gold, solar, music royalties, carbon)."""
    if not is_enabled():
        click.echo("RWAiSE is disabled.", err=True)
        raise SystemExit(1)
    from .seed_demo import run
    run(investors=investors, with_orderbook=with_orderbook)


@rwaise_cli.command("seed-reset")
@click.confirmation_option(prompt="Drop ALL demo data?")
def seed_reset():
    if not is_enabled():
        click.echo("RWAiSE is disabled.", err=True)
        raise SystemExit(1)
    from .seed_demo import reset
    reset()
