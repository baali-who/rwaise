"""Developer-facing API platform for RWAiSE.

Three blueprints:
  - :mod:`oauth`              — OAuth 2.0 client_credentials issuer
  - :mod:`public.routes`      — paid public API (gated by OAuth + x402)
  - :mod:`developer_portal.routes` — UI to register apps + see usage
"""
