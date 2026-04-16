"""PaymentConfig — injectable configuration for the UPI payment plugin."""

from dataclasses import dataclass, field


@dataclass
class PaymentConfig:
    """All settings needed to run the UPI payment system in any Telegram bot.

    Required fields have no default and must always be supplied.
    Optional fields have sensible defaults that match BharatPe's standard setup.

    Example::

        from payment_plugin import PaymentConfig, register_payment_handlers

        cfg = PaymentConfig(
            upi_id="yourname@bank",
            merchant_name="My Store",
            merchant_id="12345678",
            api_token="...",
            api_cookie="...",
            db_url="postgresql://...",
            admin_ids=[123456789],
        )
        register_payment_handlers(app, cfg)
    """

    # ── Merchant identity ──────────────────────────────────────────────────
    upi_id: str
    merchant_name: str

    # ── BharatPe API ──────────────────────────────────────────────────────
    merchant_id: str
    api_token: str
    api_cookie: str

    bharatpe_api: str = (
        "https://payments-tesseract.bharatpe.in/api/v1/merchant/transactions"
    )
    user_agent: str = (
        "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Mobile Safari/537.36"
    )

    # ── Database ──────────────────────────────────────────────────────────
    db_url: str = "postgresql://postgres:postgres@localhost:5432/pay0_bot"

    # ── Payment behaviour ────────────────────────────────────────────────
    timeout: int = 300          # seconds until QR expires (default: 5 min)
    poll_interval: int = 4      # seconds between BharatPe API checks
    min_amount: float = 1.0     # minimum payment amount in ₹
    max_amount: float = 50000.0 # maximum payment amount in ₹

    # ── Rate limits ──────────────────────────────────────────────────────
    max_per_hour: int = 10      # max payments per user per hour
    max_concurrent: int = 3     # max simultaneous pending payments per user

    # ── Admin Telegram user IDs ──────────────────────────────────────────
    admin_ids: list[int] = field(default_factory=list)
