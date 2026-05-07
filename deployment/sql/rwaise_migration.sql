-- =============================================================
-- Gopnik Wallet — RWAiSE plugin schema delta
-- Generated: 2026-05-06T14:59:24.276749Z
-- Tables:    18
-- Source:    gopnik/rwaise/models.py
--
-- Apply on top of an existing iter-13B schema (the full
-- gopnik schema with 164 tables). Safe to re-run.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS, CREATE INDEX IF
-- NOT EXISTS, CREATE TYPE wrapped in DO/EXCEPTION for
-- duplicate_object.
-- =============================================================

BEGIN;

-- ENUM: mptissuancestatus
DO $$
BEGIN
  CREATE TYPE mptissuancestatus AS ENUM ('draft', 'submitted', 'validated', 'locked', 'closed');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ENUM: mptholderstatus
DO $$
BEGIN
  CREATE TYPE mptholderstatus AS ENUM ('pending_optin', 'pending_authorize', 'active', 'locked', 'revoked');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ENUM: mptauditeventkind
DO $$
BEGIN
  CREATE TYPE mptauditeventkind AS ENUM ('issuance_create', 'holder_authorize', 'issuer_authorize', 'distribution', 'secondary_transfer', 'lock_holder', 'unlock_holder', 'lock_issuance', 'unlock_issuance', 'clawback', 'redemption_burn', 'metadata_audit', 'escrow_create', 'escrow_finish', 'escrow_cancel', 'redemption_request', 'redemption_approve', 'redemption_reject', 'redemption_release', 'redemption_cancel');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ENUM: mptescrowstatus
DO $$
BEGIN
  CREATE TYPE mptescrowstatus AS ENUM ('pending', 'finished', 'cancelled');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ENUM: mptredemptionstatus
DO $$
BEGIN
  CREATE TYPE mptredemptionstatus AS ENUM ('requested', 'awaiting_4eyes', 'approved', 'rejected', 'released', 'cancelled');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ENUM: redemptionpipelinestage
DO $$
BEGIN
  CREATE TYPE redemptionpipelinestage AS ENUM ('requested', 'sanctioned_check', 'awaiting_approver1', 'awaiting_approver2', 'fiat_payout', 'burn', 'completed', 'rejected');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ENUM: credentialattributekind
DO $$
BEGIN
  CREATE TYPE credentialattributekind AS ENUM ('base', 'jurisdiction', 'accreditation', 'asset_class', 'professional', 'institutional');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ENUM: orderside
DO $$
BEGIN
  CREATE TYPE orderside AS ENUM ('buy', 'sell');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ENUM: ordertype
DO $$
BEGIN
  CREATE TYPE ordertype AS ENUM ('limit', 'market', 'rfq');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ENUM: orderstatus
DO $$
BEGIN
  CREATE TYPE orderstatus AS ENUM ('open', 'partially_filled', 'filled', 'cancelled', 'rejected');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ENUM: developerclientstatus
DO $$
BEGIN
  CREATE TYPE developerclientstatus AS ENUM ('pending_review', 'active', 'suspended', 'revoked');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS rwaise_mpt_issuance (
	id SERIAL NOT NULL, 
	issuance_id VARCHAR(48), 
	issuer_account VARCHAR(64) NOT NULL, 
	asset_scale INTEGER NOT NULL, 
	maximum_amount BIGINT, 
	transfer_fee INTEGER NOT NULL, 
	can_lock BOOLEAN NOT NULL, 
	require_auth BOOLEAN NOT NULL, 
	can_escrow BOOLEAN NOT NULL, 
	can_trade BOOLEAN NOT NULL, 
	can_transfer BOOLEAN NOT NULL, 
	can_clawback BOOLEAN NOT NULL, 
	metadata_json JSON, 
	metadata_hash VARCHAR(64), 
	metadata_uri VARCHAR(255), 
	status mptissuancestatus NOT NULL, 
	submitted_tx_hash VARCHAR(64), 
	validated_at TIMESTAMP WITHOUT TIME ZONE, 
	closed_at TIMESTAMP WITHOUT TIME ZONE, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_rwaise_mpt_issuance_issuance_id ON rwaise_mpt_issuance (issuance_id);

CREATE INDEX IF NOT EXISTS ix_rwaise_mpt_issuance_issuer_account ON rwaise_mpt_issuance (issuer_account);

CREATE TABLE IF NOT EXISTS rwaise_credential_attribute_type (
	id SERIAL NOT NULL, 
	symbol VARCHAR(40) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	kind credentialattributekind NOT NULL, 
	description TEXT, 
	issuance_id VARCHAR(48), 
	soulbound BOOLEAN NOT NULL, 
	revocable BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (symbol)
);

CREATE INDEX IF NOT EXISTS ix_rwaise_credential_attribute_type_issuance_id ON rwaise_credential_attribute_type (issuance_id);

CREATE INDEX IF NOT EXISTS ix_rwaise_credential_attribute_type_symbol ON rwaise_credential_attribute_type (symbol);

CREATE TABLE IF NOT EXISTS rwaise_orderbook_order (
	id SERIAL NOT NULL, 
	listing_id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	wallet VARCHAR(64) NOT NULL, 
	side orderside NOT NULL, 
	type ordertype NOT NULL, 
	units_total BIGINT NOT NULL, 
	units_remaining BIGINT NOT NULL, 
	price_per_unit FLOAT NOT NULL, 
	quote_currency VARCHAR(12) NOT NULL, 
	permissioned_domain_id VARCHAR(64), 
	xls66_offer_index VARCHAR(64), 
	status orderstatus NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	closed_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_rwaise_orderbook_order_user_id ON rwaise_orderbook_order (user_id);

CREATE INDEX IF NOT EXISTS ix_rwaise_orderbook_order_listing_id ON rwaise_orderbook_order (listing_id);

CREATE TABLE IF NOT EXISTS rwaise_odl_route (
	id SERIAL NOT NULL, 
	source_currency VARCHAR(40) NOT NULL, 
	destination_currency VARCHAR(40) NOT NULL, 
	bridge_mpt_issuance_id VARCHAR(48) NOT NULL, 
	bridge_symbol VARCHAR(40) NOT NULL, 
	notional_usd_min FLOAT NOT NULL, 
	notional_usd_max FLOAT NOT NULL, 
	typical_spread_bps INTEGER NOT NULL, 
	enabled BOOLEAN NOT NULL, 
	last_priced_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_rwaise_odl_route_destination_currency ON rwaise_odl_route (destination_currency);

CREATE INDEX IF NOT EXISTS ix_rwaise_odl_route_bridge_mpt_issuance_id ON rwaise_odl_route (bridge_mpt_issuance_id);

CREATE INDEX IF NOT EXISTS ix_rwaise_odl_route_source_currency ON rwaise_odl_route (source_currency);

CREATE TABLE IF NOT EXISTS rwaise_developer_client (
	id SERIAL NOT NULL, 
	owner_user_id INTEGER NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	description TEXT, 
	homepage_url VARCHAR(255), 
	logo_s3_key VARCHAR(255), 
	client_id VARCHAR(64) NOT NULL, 
	client_secret_hash VARCHAR(255) NOT NULL, 
	scopes JSON NOT NULL, 
	rate_limit_per_minute INTEGER NOT NULL, 
	daily_spend_cap_drops BIGINT NOT NULL, 
	status developerclientstatus NOT NULL, 
	suspended_reason TEXT, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	approved_at TIMESTAMP WITHOUT TIME ZONE, 
	revoked_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_rwaise_developer_client_owner_user_id ON rwaise_developer_client (owner_user_id);

CREATE UNIQUE INDEX IF NOT EXISTS ix_rwaise_developer_client_client_id ON rwaise_developer_client (client_id);

CREATE TABLE IF NOT EXISTS rwaise_feature_flag (
	id SERIAL NOT NULL, 
	key VARCHAR(64) NOT NULL, 
	enabled BOOLEAN NOT NULL, 
	description TEXT, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_rwaise_feature_flag_key ON rwaise_feature_flag (key);

CREATE TABLE IF NOT EXISTS rwaise_feature_flag_audit (
	id SERIAL NOT NULL, 
	key VARCHAR(64) NOT NULL, 
	value BOOLEAN NOT NULL, 
	actor_user_id INTEGER, 
	reason TEXT, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_rwaise_feature_flag_audit_key ON rwaise_feature_flag_audit (key);

CREATE TABLE IF NOT EXISTS rwaise_agent_cap (
	id SERIAL NOT NULL, 
	user_id INTEGER NOT NULL, 
	daily_buy_cap_usd INTEGER NOT NULL, 
	daily_sell_cap_usd INTEGER NOT NULL, 
	transfer_cap_drops BIGINT NOT NULL, 
	require_2fa_above_drops BIGINT NOT NULL, 
	enabled BOOLEAN NOT NULL, 
	last_updated_by INTEGER, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_rwaise_agent_cap_user_id ON rwaise_agent_cap (user_id);

CREATE TABLE IF NOT EXISTS rwaise_mpt_holder (
	id SERIAL NOT NULL, 
	issuance_id INTEGER NOT NULL, 
	holder_account VARCHAR(64) NOT NULL, 
	status mptholderstatus NOT NULL, 
	last_observed_balance BIGINT NOT NULL, 
	is_locked BOOLEAN NOT NULL, 
	authorized_at TIMESTAMP WITHOUT TIME ZONE, 
	revoked_at TIMESTAMP WITHOUT TIME ZONE, 
	last_seen_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (issuance_id, holder_account), 
	FOREIGN KEY(issuance_id) REFERENCES rwaise_mpt_issuance (id)
);

CREATE INDEX IF NOT EXISTS ix_rwaise_mpt_holder_issuance_id ON rwaise_mpt_holder (issuance_id);

CREATE INDEX IF NOT EXISTS ix_rwaise_mpt_holder_holder_account ON rwaise_mpt_holder (holder_account);

CREATE TABLE IF NOT EXISTS rwaise_mpt_audit_event (
	id SERIAL NOT NULL, 
	issuance_id INTEGER, 
	holder_account VARCHAR(64), 
	kind mptauditeventkind NOT NULL, 
	actor_user_id INTEGER, 
	tx_hash VARCHAR(64), 
	payload_json JSON, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(issuance_id) REFERENCES rwaise_mpt_issuance (id)
);

CREATE INDEX IF NOT EXISTS ix_rwaise_mpt_audit_event_issuance_id ON rwaise_mpt_audit_event (issuance_id);

CREATE TABLE IF NOT EXISTS rwaise_mpt_escrow (
	id SERIAL NOT NULL, 
	issuance_id INTEGER NOT NULL, 
	sender_account VARCHAR(64) NOT NULL, 
	destination_account VARCHAR(64) NOT NULL, 
	amount BIGINT NOT NULL, 
	finish_after TIMESTAMP WITHOUT TIME ZONE, 
	cancel_after TIMESTAMP WITHOUT TIME ZONE, 
	condition_hex VARCHAR(255), 
	on_ledger_sequence BIGINT, 
	status mptescrowstatus NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(issuance_id) REFERENCES rwaise_mpt_issuance (id)
);

CREATE INDEX IF NOT EXISTS ix_rwaise_mpt_escrow_issuance_id ON rwaise_mpt_escrow (issuance_id);

CREATE TABLE IF NOT EXISTS rwaise_mpt_redemption (
	id SERIAL NOT NULL, 
	issuance_id INTEGER NOT NULL, 
	holder_user_id INTEGER NOT NULL, 
	holder_account VARCHAR(64) NOT NULL, 
	amount BIGINT NOT NULL, 
	fiat_payout_currency VARCHAR(8) NOT NULL, 
	fiat_payout_amount FLOAT, 
	bank_payout_reference VARCHAR(64), 
	status mptredemptionstatus NOT NULL, 
	approver1_user_id INTEGER, 
	approver2_user_id INTEGER, 
	approver1_at TIMESTAMP WITHOUT TIME ZONE, 
	approver2_at TIMESTAMP WITHOUT TIME ZONE, 
	rejection_reason TEXT, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(issuance_id) REFERENCES rwaise_mpt_issuance (id)
);

CREATE INDEX IF NOT EXISTS ix_rwaise_mpt_redemption_issuance_id ON rwaise_mpt_redemption (issuance_id);

CREATE TABLE IF NOT EXISTS rwaise_credential_grant (
	id SERIAL NOT NULL, 
	user_id INTEGER NOT NULL, 
	credential_type_id INTEGER NOT NULL, 
	holder_wallet VARCHAR(64) NOT NULL, 
	granted_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	revoked_at TIMESTAMP WITHOUT TIME ZONE, 
	grant_reason TEXT, 
	revoke_reason TEXT, 
	grant_tx_hash VARCHAR(64), 
	revoke_tx_hash VARCHAR(64), 
	PRIMARY KEY (id), 
	FOREIGN KEY(credential_type_id) REFERENCES rwaise_credential_attribute_type (id), 
	UNIQUE (user_id, credential_type_id)
);

CREATE INDEX IF NOT EXISTS ix_rwaise_credential_grant_credential_type_id ON rwaise_credential_grant (credential_type_id);

CREATE INDEX IF NOT EXISTS ix_rwaise_credential_grant_user_id ON rwaise_credential_grant (user_id);

CREATE TABLE IF NOT EXISTS rwaise_marketplace_credential_req (
	id SERIAL NOT NULL, 
	listing_id INTEGER NOT NULL, 
	credential_type_id INTEGER NOT NULL, 
	note VARCHAR(255), 
	PRIMARY KEY (id), 
	FOREIGN KEY(credential_type_id) REFERENCES rwaise_credential_attribute_type (id), 
	UNIQUE (listing_id, credential_type_id)
);

CREATE INDEX IF NOT EXISTS ix_rwaise_marketplace_credential_req_listing_id ON rwaise_marketplace_credential_req (listing_id);

CREATE TABLE IF NOT EXISTS rwaise_orderbook_fill (
	id SERIAL NOT NULL, 
	buy_order_id INTEGER NOT NULL, 
	sell_order_id INTEGER NOT NULL, 
	units BIGINT NOT NULL, 
	price_per_unit FLOAT NOT NULL, 
	settlement_tx_hash VARCHAR(64), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(sell_order_id) REFERENCES rwaise_orderbook_order (id), 
	FOREIGN KEY(buy_order_id) REFERENCES rwaise_orderbook_order (id)
);

CREATE INDEX IF NOT EXISTS ix_rwaise_orderbook_fill_settlement_tx_hash ON rwaise_orderbook_fill (settlement_tx_hash);

CREATE TABLE IF NOT EXISTS rwaise_developer_oauth_token (
	id SERIAL NOT NULL, 
	client_id INTEGER NOT NULL, 
	access_token_hash VARCHAR(255) NOT NULL, 
	scope VARCHAR(255) NOT NULL, 
	issued_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	revoked_at TIMESTAMP WITHOUT TIME ZONE, 
	last_used_at TIMESTAMP WITHOUT TIME ZONE, 
	use_count INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (access_token_hash), 
	FOREIGN KEY(client_id) REFERENCES rwaise_developer_client (id)
);

CREATE INDEX IF NOT EXISTS ix_rwaise_developer_oauth_token_client_id ON rwaise_developer_oauth_token (client_id);

CREATE TABLE IF NOT EXISTS rwaise_redemption_pipeline (
	id SERIAL NOT NULL, 
	ticket_id INTEGER NOT NULL, 
	stage redemptionpipelinestage NOT NULL, 
	sf_execution_arn VARCHAR(255), 
	last_event_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	notes TEXT, 
	PRIMARY KEY (id), 
	UNIQUE (ticket_id), 
	FOREIGN KEY(ticket_id) REFERENCES rwaise_mpt_redemption (id)
);

CREATE TABLE IF NOT EXISTS rwaise_developer_api_call_log (
	id SERIAL NOT NULL, 
	client_id INTEGER NOT NULL, 
	token_id INTEGER, 
	method VARCHAR(8) NOT NULL, 
	path VARCHAR(255) NOT NULL, 
	status_code INTEGER NOT NULL, 
	latency_ms INTEGER NOT NULL, 
	paid BOOLEAN NOT NULL, 
	payment_scheme VARCHAR(16), 
	payment_amount VARCHAR(40), 
	payment_tx_hash VARCHAR(64), 
	request_id VARCHAR(48), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(token_id) REFERENCES rwaise_developer_oauth_token (id), 
	FOREIGN KEY(client_id) REFERENCES rwaise_developer_client (id)
);

CREATE INDEX IF NOT EXISTS ix_rwaise_developer_api_call_log_payment_tx_hash ON rwaise_developer_api_call_log (payment_tx_hash);

CREATE INDEX IF NOT EXISTS ix_rwaise_developer_api_call_log_client_id ON rwaise_developer_api_call_log (client_id);

CREATE INDEX IF NOT EXISTS ix_rwaise_developer_api_call_log_path ON rwaise_developer_api_call_log (path);

-- =============================================================
-- Verify after apply:
-- Expected 18 rwaise_* tables:
--   rwaise_agent_cap
--   rwaise_credential_attribute_type
--   rwaise_credential_grant
--   rwaise_developer_api_call_log
--   rwaise_developer_client
--   rwaise_developer_oauth_token
--   rwaise_feature_flag
--   rwaise_feature_flag_audit
--   rwaise_marketplace_credential_req
--   rwaise_mpt_audit_event
--   rwaise_mpt_escrow
--   rwaise_mpt_holder
--   rwaise_mpt_issuance
--   rwaise_mpt_redemption
--   rwaise_odl_route
--   rwaise_orderbook_fill
--   rwaise_orderbook_order
--   rwaise_redemption_pipeline
--
-- Run:
--   SELECT count(*) FROM information_schema.tables
--    WHERE table_schema='public' AND table_name LIKE 'rwaise_%%'; -- expect 18
-- =============================================================

COMMIT;
