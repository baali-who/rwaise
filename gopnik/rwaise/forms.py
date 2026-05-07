"""WTForms classes for the 9-step tokenisation wizard.

Each step's form lives below with explicit validators. Wizard
controller imports these via ``WIZARD_FORMS`` mapping and uses
``form.validate()`` per step.
"""
from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField, DecimalField, FieldList, FileField, FormField,
    HiddenField, IntegerField, SelectField, StringField, TextAreaField,
)
from wtforms.validators import (
    DataRequired, Email, Length, NumberRange, Optional, Regexp,
)


# ─── Step 1 — Asset class ────────────────────────────────────────────


class Step1AssetClassForm(FlaskForm):
    asset_class = SelectField(
        "Asset class",
        choices=[
            ("real_estate", "Real estate"),
            ("debt", "Debt instruments / trade finance"),
            ("commodities", "Commodities (gold, silver, oil)"),
            ("carbon", "Carbon credits"),
            ("intellectual_property", "Intellectual property"),
            ("private_equity", "Private equity"),
            ("art", "Art / collectibles"),
            ("other", "Other"),
        ],
        validators=[DataRequired()],
    )
    subclass = StringField("Subclass", validators=[Optional(), Length(max=80)])
    description = TextAreaField(
        "Description",
        validators=[DataRequired(), Length(min=20, max=2000)],
    )


# ─── Step 2 — Legal & SPV ────────────────────────────────────────────


class Step2LegalForm(FlaskForm):
    spv_name = StringField("SPV / issuer name", validators=[DataRequired(), Length(max=120)])
    spv_jurisdiction = SelectField(
        "Jurisdiction of incorporation",
        choices=[
            ("US-DE", "United States — Delaware"),
            ("US-WY", "United States — Wyoming"),
            ("LU", "Luxembourg"),
            ("LI", "Liechtenstein"),
            ("KY", "Cayman Islands"),
            ("BVI", "British Virgin Islands"),
            ("SG", "Singapore"),
            ("CH", "Switzerland"),
            ("OTHER", "Other"),
        ],
        validators=[DataRequired()],
    )
    legal_counsel_email = StringField(
        "Legal counsel email",
        validators=[Optional(), Email(), Length(max=160)],
    )


# ─── Step 3 — Documents ──────────────────────────────────────────────


class Step3DocumentsForm(FlaskForm):
    prospectus = FileField("Prospectus (PDF)", validators=[Optional()])
    spa = FileField("Subscription agreement (PDF)", validators=[Optional()])
    audit_report = FileField("Audit / valuation report (PDF)",
                              validators=[Optional()])


# ─── Step 4 — Valuation ──────────────────────────────────────────────


class Step4ValuationForm(FlaskForm):
    valuation_usd = DecimalField(
        "Total valuation (USD)",
        validators=[DataRequired(), NumberRange(min=1)],
        places=2,
    )
    target_apy_bps = IntegerField(
        "Target APY (basis points)",
        validators=[Optional(), NumberRange(min=0, max=10_000)],
    )
    valuation_methodology = SelectField(
        "Valuation methodology",
        choices=[
            ("dcf", "Discounted cash flow"),
            ("market_comp", "Market comparable"),
            ("third_party", "Third-party appraiser"),
            ("nav", "Net asset value"),
            ("other", "Other"),
        ],
        validators=[DataRequired()],
    )


# ─── Step 5 — KYC requirements ───────────────────────────────────────


class Step5KYCForm(FlaskForm):
    require_credential = BooleanField("Require XLS-65 verified-investor credential",
                                       default=True)
    require_jurisdiction = BooleanField("Require jurisdiction credential", default=False)
    require_accreditation = BooleanField("Require accreditation credential",
                                          default=False)
    minimum_holding = DecimalField("Minimum holding (USD)",
                                    validators=[Optional(), NumberRange(min=0)],
                                    places=2, default=0)


# ─── Step 6 — Token parameters ───────────────────────────────────────


class Step6TokenParamsForm(FlaskForm):
    name = StringField("Token name", validators=[DataRequired(), Length(max=80)])
    symbol = StringField(
        "Symbol",
        validators=[
            DataRequired(),
            Length(min=2, max=10),
            Regexp(r"^[A-Z0-9]+$",
                   message="Uppercase letters and digits only."),
        ],
    )
    asset_scale = IntegerField(
        "Asset scale (decimal places)",
        default=2,
        validators=[NumberRange(min=0, max=15)],
    )
    maximum_amount = IntegerField(
        "Maximum supply (in smallest units)",
        validators=[Optional(), NumberRange(min=0)],
    )
    transfer_fee_bps = IntegerField(
        "Transfer fee (basis points, 0-1000)",
        default=0,
        validators=[NumberRange(min=0, max=1000)],
    )
    can_lock = BooleanField("Allow issuer to lock holders", default=True)
    can_escrow = BooleanField("Allow escrow", default=True)
    can_trade = BooleanField("Allow secondary trading", default=True)
    can_transfer = BooleanField("Allow transfers", default=True)
    can_clawback = BooleanField("Allow clawback (sanctions / compliance)", default=True)


# ─── Step 7 — Cap table ──────────────────────────────────────────────


class Step7CapTableForm(FlaskForm):
    """Step 7 is mostly an upload; the JSON preview lives in /wizard/cap-table/parse."""
    cap_table = FileField("Cap-table CSV")
    high_notional_threshold_units = IntegerField(
        "Soft-flag threshold (units)",
        default=100_000,
        validators=[NumberRange(min=0)],
    )


# ─── Step 8 — Marketplace settings ───────────────────────────────────


class Step8MarketplaceForm(FlaskForm):
    quote_currency = SelectField(
        "Quote currency",
        choices=[
            ("USD", "USD (RLUSD on-ledger)"),
            ("EUR", "EUR"),
            ("XRP", "XRP"),
        ],
        validators=[DataRequired()],
    )
    enable_orderbook = BooleanField("Enable XLS-66-ready order book", default=True)
    enable_redemption = BooleanField("Enable 4-eyes redemption", default=True)


# ─── Step 9 — Review & finalize ──────────────────────────────────────


class Step9ReviewForm(FlaskForm):
    confirm_legal_review = BooleanField(
        "Counsel has reviewed every wizard step",
        validators=[DataRequired()],
    )
    confirm_audit = BooleanField(
        "An independent audit / valuation report is on file",
        validators=[DataRequired()],
    )
    confirm_understand_clawback = BooleanField(
        "I understand can_clawback=True allows the issuer to seize tokens",
        validators=[DataRequired()],
    )
