"""Regression checks for local tracked-file secret scanning."""

from scripts.check_secret_hygiene import violations


def test_secret_scanner_rejects_env_private_key_and_nonempty_token() -> None:
    assert "tracked_env_file" in violations(".env", b"TRADING_MODE=paper")
    assert "tracked_env_file" in violations("nested/.env.local", b"")
    assert "private_key_block" in violations(
        "docs/example.txt", b"-----BEGIN " + b"RSA PRIVATE KEY-----\nsecret\n"
    )
    assert "nonempty_secret_assignment" in violations(
        "config.toml", b"BINANCE_API_KEY=abc123\n"
    )


def test_secret_scanner_allows_keyless_example_and_public_data() -> None:
    assert violations(".env.example", b"T_INVEST_API_TOKEN=\nTRADING_MODE=paper\n") == ()
    assert violations("docs/readme.md", b"Binance public API has no key.\n") == ()
