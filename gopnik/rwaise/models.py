"""SQLAlchemy models contributed by the RWAiSE plugin.

Every table is added to Gopnik's existing ``db`` instance, with the
``rwaise_`` prefix so an operator can inspect the plugin's schema in
isolation::

    SELECT relname FROM pg_class
    WHERE relname LIKE 'rwaise_%' AND relkind='r';

Tables introduced
=================

  rwaise_mpt_issuance               XLS-33 issuance lifecycle
  rwaise_mpt_holder                 (issuance × holder) state mirror
  rwaise_mpt_audit_event            append-only audit log
  rwaise_mpt_escrow                 MPT-aware escrow tickets
  rwaise_mpt_redemption             4-eyes redemption tickets
  rwaise_redemption_pipeline        Step-Functions stage mirror
  rwaise_credential_attribute_type  XLS-65 credential catalogue
  rwaise_credential_grant           user × credential bindings
  rwaise_marketplace_credential_req listing × credential requirement
  rwaise_orderbook_order            XLS-66-ready order book
  rwaise_orderbook_fill             matcher output
  rwaise_odl_route                  XLS-71 ODL bridge routes
  rwaise_developer_client           OAuth 2.0 clients
  rwaise_developer_oauth_token      issued tokens (audited)
  rwaise_developer_api_call_log     per-call audit trail
  rwaise_agent_cap                  per-user Bedrock-agent caps

The plugin **does not** modify Gopnik's existing tables. We rely on
``gopnik.User``, ``gopnik.Wallet``, ``gopnik.RWAAsset`` and friends as
foreign-key targets — see the imports below.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, Boolean, DateTime, Float,
    ForeignKey, Index, UniqueConstraint, JSON, Enum,
)
from sqlalchemy.orm import relationship

# Reuse Gopnik's shared db + the host's existing User / Wallet / RWAAsset.
# These imports raise ImportError if the plugin is dropped into a tree
# that doesn't have Gopnik — fail fast and loud.
from gopnik.models import db  # type: ignore[attr-defined]


# ─── Enumerations (kept stable across migrations) ───────────────────


class MPTIssuanceStatus(str, enum.Enum):
    draft = "draft"
    submitted = "submitted"
    validated = "validated"
    locked = "locked"
    closed = "closed"


class MPTHolderStatus(str, enum.Enum):
    pending_optin = "pending_optin"
    pending_authorize = "pending_authorize"
    active = "active"
    locked = "locked"
    revoked = "revoked"


class MPTAuditEventKind(str, enum.Enum):
    issuance_create = "issuance_create"
    holder_authorize = "holder_authorize"
    issuer_authorize = "issuer_authorize"
    distribution = "distribution"
    secondary_transfer = "secondary_transfer"
    lock_holder = "lock_holder"
    unlock_holder = "unlock_holder"
    lock_issuance = "lock_issuance"
    unlock_issuance = "unlock_issuance"
    clawback = "clawback"
    redemption_burn = "redemption_burn"
    metadata_audit = "metadata_audit"
    escrow_create = "escrow_create"
    escrow_finish = "escrow_finish"
    escrow_cancel = "escrow_cancel"
    redemption_request = "redemption_request"
    redemption_approve = "redemption_approve"
    redemption_reject = "redemption_reject"
    redemption_release = "redemption_release"
    redemption_cancel = "redemption_cancel"


class MPTEscrowStatus(str, enum.Enum):
    pending = "pending"
    finished = "finished"
    cancelled = "cancelled"


class MPTRedemptionStatus(str, enum.Enum):
    requested = "requested"
    awaiting_4eyes = "awaiting_4eyes"
    approved = "approved"
    rejected = "rejected"
    released = "released"
    cancelled = "cancelled"


class CredentialAttributeKind(str, enum.Enum):
    base = "base"
    jurisdiction = "jurisdiction"
    accreditation = "accreditation"
    asset_class = "asset_class"
    professional = "professional"
    institutional = "institutional"


class OrderSide(str, enum.Enum):
    buy = "buy"
    sell = "sell"


class OrderStatus(str, enum.Enum):
    open = "open"
    partially_filled = "partially_filled"
    filled = "filled"
    cancelled = "cancelled"
    rejected = "rejected"


class OrderType(str, enum.Enum):
    limit = "limit"
    market = "market"
    rfq = "rfq"


class RedemptionPipelineStage(str, enum.Enum):
    requested = "requested"
    sanctioned_check = "sanctioned_check"
    awaiting_approver1 = "awaiting_approver1"
    awaiting_approver2 = "awaiting_approver2"
    fiat_payout = "fiat_payout"
    burn = "burn"
    completed = "completed"
    rejected = "rejected"


class DeveloperClientStatus(str, enum.Enum):
    pending_review = "pending_review"
    active = "active"
    suspended = "suspended"
    revoked = "revoked"


# ─── MPT lifecycle ──────────────────────────────────────────────────


class MPTIssuance(db.Model):
    __tablename__ = "rwaise_mpt_issuance"

    id = Column(Integer, primary_key=True)
    issuance_id = Column(String(48), unique=True, index=True, nullable=True)
    issuer_account = Column(String(64), index=True, nullable=False)
    asset_scale = Column(Integer, nullable=False, default=0)
    maximum_amount = Column(BigInteger, nullable=True)
    transfer_fee = Column(Integer, nullable=False, default=0)
    can_lock = Column(Boolean, nullable=False, default=True)
    require_auth = Column(Boolean, nullable=False, default=True)
    can_escrow = Column(Boolean, nullable=False, default=True)
    can_trade = Column(Boolean, nullable=False, default=True)
    can_transfer = Column(Boolean, nullable=False, default=True)
    can_clawback = Column(Boolean, nullable=False, default=True)
    metadata_json = Column(JSON, nullable=True)
    metadata_hash = Column(String(64), nullable=True)
    metadata_uri = Column(String(255), nullable=True)
    status = Column(Enum(MPTIssuanceStatus), nullable=False,
                    default=MPTIssuanceStatus.draft)
    submitted_tx_hash = Column(String(64), nullable=True)
    validated_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    # Iter-15 hotfix: gopnik.rwa.models also declares classes named
    # MPTHolder, MPTIssuance, MPTAuditEvent (iter-3 RWA package). Both
    # registries share Gopnik's single ``db`` instance, so the bare
    # string "MPTHolder" is ambiguous and SQLAlchemy refuses to
    # initialise the mappers (InvalidRequestError on first .query).
    # Fully-qualifying the path with the module prefix removes the
    # ambiguity. See https://docs.sqlalchemy.org/en/20/orm/relationship_api.html
    # "When a relationship() argument is a string, the lookup is
    # against any ORM class registered in the declarative base; if more
    # than one match is found the lookup fails."
    holders = relationship("gopnik.rwaise.models.MPTHolder",
                           back_populates="issuance",
                           cascade="all,delete-orphan")
    audit_events = relationship("gopnik.rwaise.models.MPTAuditEvent",
                                back_populates="issuance")


class MPTHolder(db.Model):
    __tablename__ = "rwaise_mpt_holder"
    __table_args__ = (UniqueConstraint("issuance_id", "holder_account"),)

    id = Column(Integer, primary_key=True)
    issuance_id = Column(Integer, ForeignKey("rwaise_mpt_issuance.id"),
                         nullable=False, index=True)
    holder_account = Column(String(64), nullable=False, index=True)
    status = Column(Enum(MPTHolderStatus), nullable=False,
                    default=MPTHolderStatus.pending_optin)
    last_observed_balance = Column(BigInteger, default=0, nullable=False)
    is_locked = Column(Boolean, default=False, nullable=False)
    authorized_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    last_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    issuance = relationship("gopnik.rwaise.models.MPTIssuance",
                             back_populates="holders")


class MPTAuditEvent(db.Model):
    __tablename__ = "rwaise_mpt_audit_event"

    id = Column(Integer, primary_key=True)
    issuance_id = Column(Integer, ForeignKey("rwaise_mpt_issuance.id"),
                         nullable=True, index=True)
    holder_account = Column(String(64), nullable=True)
    kind = Column(Enum(MPTAuditEventKind), nullable=False)
    actor_user_id = Column(Integer, nullable=True)
    tx_hash = Column(String(64), nullable=True)
    payload_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    issuance = relationship("gopnik.rwaise.models.MPTIssuance",
                             back_populates="audit_events")


class MPTEscrow(db.Model):
    __tablename__ = "rwaise_mpt_escrow"

    id = Column(Integer, primary_key=True)
    issuance_id = Column(Integer, ForeignKey("rwaise_mpt_issuance.id"),
                         nullable=False, index=True)
    sender_account = Column(String(64), nullable=False)
    destination_account = Column(String(64), nullable=False)
    amount = Column(BigInteger, nullable=False)
    finish_after = Column(DateTime, nullable=True)
    cancel_after = Column(DateTime, nullable=True)
    condition_hex = Column(String(255), nullable=True)
    on_ledger_sequence = Column(BigInteger, nullable=True)
    status = Column(Enum(MPTEscrowStatus), nullable=False,
                    default=MPTEscrowStatus.pending)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class MPTRedemptionTicket(db.Model):
    __tablename__ = "rwaise_mpt_redemption"

    id = Column(Integer, primary_key=True)
    issuance_id = Column(Integer, ForeignKey("rwaise_mpt_issuance.id"),
                         nullable=False, index=True)
    holder_user_id = Column(Integer, nullable=False)
    holder_account = Column(String(64), nullable=False)
    amount = Column(BigInteger, nullable=False)
    fiat_payout_currency = Column(String(8), nullable=False, default="USD")
    fiat_payout_amount = Column(Float, nullable=True)
    bank_payout_reference = Column(String(64), nullable=True)
    status = Column(Enum(MPTRedemptionStatus), nullable=False,
                    default=MPTRedemptionStatus.requested)
    approver1_user_id = Column(Integer, nullable=True)
    approver2_user_id = Column(Integer, nullable=True)
    approver1_at = Column(DateTime, nullable=True)
    approver2_at = Column(DateTime, nullable=True)
    rejection_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)


class RedemptionPipeline(db.Model):
    __tablename__ = "rwaise_redemption_pipeline"

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("rwaise_mpt_redemption.id"),
                       nullable=False, unique=True)
    stage = Column(Enum(RedemptionPipelineStage), nullable=False,
                   default=RedemptionPipelineStage.requested)
    sf_execution_arn = Column(String(255), nullable=True)
    last_event_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    notes = Column(Text, nullable=True)


# ─── Layered credential model (XLS-65) ───────────────────────────────


class CredentialAttributeType(db.Model):
    __tablename__ = "rwaise_credential_attribute_type"
    __table_args__ = (UniqueConstraint("symbol"),)

    id = Column(Integer, primary_key=True)
    symbol = Column(String(40), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    kind = Column(Enum(CredentialAttributeKind), nullable=False)
    description = Column(Text, nullable=True)
    issuance_id = Column(String(48), nullable=True, index=True)
    soulbound = Column(Boolean, default=True, nullable=False)
    revocable = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class CredentialGrant(db.Model):
    __tablename__ = "rwaise_credential_grant"
    __table_args__ = (UniqueConstraint("user_id", "credential_type_id"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    credential_type_id = Column(Integer,
                                ForeignKey("rwaise_credential_attribute_type.id"),
                                nullable=False, index=True)
    holder_wallet = Column(String(64), nullable=False)
    granted_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    grant_reason = Column(Text, nullable=True)
    revoke_reason = Column(Text, nullable=True)
    grant_tx_hash = Column(String(64), nullable=True)
    revoke_tx_hash = Column(String(64), nullable=True)


class MarketplaceCredentialRequirement(db.Model):
    __tablename__ = "rwaise_marketplace_credential_req"
    __table_args__ = (UniqueConstraint("listing_id", "credential_type_id"),)

    id = Column(Integer, primary_key=True)
    listing_id = Column(Integer, nullable=False, index=True,
                        doc="FK to gopnik's marketplace_listings table")
    credential_type_id = Column(Integer,
                                ForeignKey("rwaise_credential_attribute_type.id"),
                                nullable=False)
    note = Column(String(255), nullable=True)


# ─── Order book (XLS-66 ready) ───────────────────────────────────────


class OrderbookOrder(db.Model):
    __tablename__ = "rwaise_orderbook_order"

    id = Column(Integer, primary_key=True)
    listing_id = Column(Integer, nullable=False, index=True,
                        doc="FK to gopnik's marketplace_listings table")
    user_id = Column(Integer, nullable=False, index=True)
    wallet = Column(String(64), nullable=False)
    side = Column(Enum(OrderSide), nullable=False)
    type = Column(Enum(OrderType), nullable=False, default=OrderType.limit)
    units_total = Column(BigInteger, nullable=False)
    units_remaining = Column(BigInteger, nullable=False)
    price_per_unit = Column(Float, nullable=False)
    quote_currency = Column(String(12), nullable=False)
    permissioned_domain_id = Column(String(64), nullable=True)
    xls66_offer_index = Column(String(64), nullable=True)
    status = Column(Enum(OrderStatus), nullable=False, default=OrderStatus.open)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    closed_at = Column(DateTime, nullable=True)


class OrderbookFill(db.Model):
    __tablename__ = "rwaise_orderbook_fill"

    id = Column(Integer, primary_key=True)
    buy_order_id = Column(Integer, ForeignKey("rwaise_orderbook_order.id"),
                          nullable=False)
    sell_order_id = Column(Integer, ForeignKey("rwaise_orderbook_order.id"),
                           nullable=False)
    units = Column(BigInteger, nullable=False)
    price_per_unit = Column(Float, nullable=False)
    settlement_tx_hash = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ─── ODL pilot ──────────────────────────────────────────────────────


class ODLRoute(db.Model):
    __tablename__ = "rwaise_odl_route"

    id = Column(Integer, primary_key=True)
    source_currency = Column(String(40), nullable=False, index=True)
    destination_currency = Column(String(40), nullable=False, index=True)
    bridge_mpt_issuance_id = Column(String(48), nullable=False, index=True)
    bridge_symbol = Column(String(40), nullable=False)
    notional_usd_min = Column(Float, nullable=False, default=0.0)
    notional_usd_max = Column(Float, nullable=False, default=1_000_000.0)
    typical_spread_bps = Column(Integer, nullable=False, default=10)
    enabled = Column(Boolean, default=True, nullable=False)
    last_priced_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ─── External-developer platform ────────────────────────────────────


class DeveloperClient(db.Model):
    __tablename__ = "rwaise_developer_client"

    id = Column(Integer, primary_key=True)
    owner_user_id = Column(Integer, nullable=False, index=True)
    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)
    homepage_url = Column(String(255), nullable=True)
    logo_s3_key = Column(String(255), nullable=True)
    client_id = Column(String(64), unique=True, nullable=False, index=True)
    client_secret_hash = Column(String(255), nullable=False)
    scopes = Column(JSON, nullable=False)
    rate_limit_per_minute = Column(Integer, nullable=False, default=60)
    daily_spend_cap_drops = Column(BigInteger, nullable=False, default=10_000_000_000)
    status = Column(Enum(DeveloperClientStatus), nullable=False,
                    default=DeveloperClientStatus.pending_review)
    suspended_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    approved_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)


class DeveloperOAuthToken(db.Model):
    __tablename__ = "rwaise_developer_oauth_token"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("rwaise_developer_client.id"),
                       nullable=False, index=True)
    access_token_hash = Column(String(255), nullable=False, unique=True)
    scope = Column(String(255), nullable=False)
    issued_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    use_count = Column(Integer, default=0, nullable=False)

    @staticmethod
    def make() -> tuple[str, str]:
        import hashlib, secrets as pysecrets
        plain = pysecrets.token_urlsafe(40)
        return plain, hashlib.sha256(plain.encode()).hexdigest()


class DeveloperAPICallLog(db.Model):
    __tablename__ = "rwaise_developer_api_call_log"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("rwaise_developer_client.id"),
                       nullable=False, index=True)
    token_id = Column(Integer, ForeignKey("rwaise_developer_oauth_token.id"),
                      nullable=True)
    method = Column(String(8), nullable=False)
    path = Column(String(255), nullable=False, index=True)
    status_code = Column(Integer, nullable=False)
    latency_ms = Column(Integer, nullable=False)
    paid = Column(Boolean, default=False, nullable=False)
    payment_scheme = Column(String(16), nullable=True)
    payment_amount = Column(String(40), nullable=True)
    payment_tx_hash = Column(String(64), nullable=True, index=True)
    request_id = Column(String(48), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ─── Per-user agent caps ─────────────────────────────────────────────


# ─── Feature flags (DB-backed, iter-15) ─────────────────────────────


class FeatureFlag(db.Model):
    """Cluster-wide feature flag.

    The plugin's master `RWAISE_ENABLED` env var is the operator floor;
    these rows are the cluster-wide truth that the admin panel toggles.
    Cached in Redis with 5-min TTL; local in-process cache (5s) too.
    """
    __tablename__ = "rwaise_feature_flag"

    id = Column(Integer, primary_key=True)
    key = Column(String(64), unique=True, nullable=False, index=True)
    enabled = Column(Boolean, nullable=False, default=False)
    description = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)


class FeatureFlagAudit(db.Model):
    """Append-only audit trail of every flag toggle."""
    __tablename__ = "rwaise_feature_flag_audit"

    id = Column(Integer, primary_key=True)
    key = Column(String(64), nullable=False, index=True)
    value = Column(Boolean, nullable=False)
    actor_user_id = Column(Integer, nullable=True)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ─── Per-user agent caps ─────────────────────────────────────────────


class AgentCap(db.Model):
    __tablename__ = "rwaise_agent_cap"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, unique=True, index=True)
    daily_buy_cap_usd = Column(Integer, nullable=False, default=50_000)
    daily_sell_cap_usd = Column(Integer, nullable=False, default=250_000)
    transfer_cap_drops = Column(BigInteger, nullable=False, default=100_000_000)
    require_2fa_above_drops = Column(BigInteger, nullable=False, default=10_000_000)
    enabled = Column(Boolean, default=True, nullable=False)
    last_updated_by = Column(Integer, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)
