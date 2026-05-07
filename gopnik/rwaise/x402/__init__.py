"""x402 protocol — HTTP 402 Payment Required for the XRP Ledger.

Identical to ``iteration_2/rwaise/rwaise/x402/`` with imports rewired:

  ``from rwaise.x402 import …``      → ``from gopnik.rwaise.x402 import …``
  ``from rwaise.app.extensions``     → ``from gopnik.app.extensions``  (the host's)
  ``from rwaise.rwa.services.mpt``   → ``from gopnik.rwaise.services.mpt``

See the installation manual (step 4) for the copy + sed one-liner.
"""
from .middleware import x402_protected, PaymentRequirement, X402Error
from .facilitator import verify_payment_proof
from .client import pay_and_retry, XrplPayer

__all__ = [
    "x402_protected", "PaymentRequirement", "X402Error",
    "verify_payment_proof", "pay_and_retry", "XrplPayer",
]
