# Notification Router acceptance — 2026-10-08

Implementation is on `feat/crypto-signal-data-hub-mcp` only. Main is unchanged.
LIVE TRADING OFF; paper/read-only signals only. No order endpoints, bot tokens,
exchange keys, merge, production notification schedule changes or deployment.
The existing feature PR #27 is updated rather than creating a duplicate PR for
this same branch. It remains a draft because full three-channel acceptance is blocked.

## Completed implementation stages

| Stage | Tasks completed | Changed files | Executed tests | Coverage | Issues | Next stage |
|---|---|---|---|---|---|---|
| Router, states, retries, idempotency, logs | Ordered three-channel routing, durable reservation, bounded retries, terminal recovery, seven states, per-attempt audit | `signal_watch/notifications.py`, `tests/test_notification_router.py` | 15 router tests, included in 693-test suite | Router 95.83% | Ambiguous dispatched timeout cannot safely retry; it becomes DELIVERY_UNCERTAIN | Composio boundary |
| Telegram and Email | Exact account/bot/chat, ACTIVE/schema/access/member checks, no-write health, verified receipts, authenticated-mailbox-only email | `signal_watch/composio_notifications.py`, `tests/test_composio_notifications.py` | 17 Composio tests, included in 693-test suite | Adapters 90.38% | Read-only membership preflight cannot guarantee that the next send remains permitted | Watch integration |
| Watch and legacy migration | HIGH/VERY HIGH routing, NO_SETUP and DATA_BLOCKED without sends, cached read-only diagnostics, legacy reservation protection, compact messages | `api/app.py`, `signal_watch/service.py`, `signal_watch/delivery.py`, `tests/test_signal_telegram_delivery.py` | Combined notification/Watch selection: 68 passed | Watch service 93.37% | Native runtime must bind authorized channels; the standalone service cannot create host sessions | Final verification/E2E |
| Verification and real delivery | All requested commands, coverage, deterministic Watch→Router→three-channel test; actual Router→Composio Email/Telegram test and duplicate replay check | This report and `CRYPTO_SIGNAL_NOTIFICATION_ROUTER.md` | 693 Python and 11 frontend tests passed | Backend 91.55% | Full real three-channel E2E blocked by unavailable ChatGPT Push | Bind ChatGPT Push and repeat full real E2E |

The original single-channel regression tests were migrated to the explicitly
changed contract: at-most-once delivery and sanitized uncertain errors remain
covered; arbitrary-chat acceptance is replaced by strict rejection of other chats.
No safety regression was removed to make a test pass. New assertions were executed
failing before implementation for the Composio boundary, email transport, schema
type drift, expiry during health, recovery identity and bounded message rendering.

## Commands executed on the rebased implementation

Python commands used the repository `.venv` (Python 3.12.3). Latest code verification:

| Command | Actual result |
|---|---|
| `ruff check .` | Passed |
| `mypy src` | Passed; 64 source files |
| `pytest -q --cov=src/quant_trading_platform --cov-report=json:/tmp/crypto-notification-coverage.json` | 693 passed; 1 Starlette TestClient deprecation warning; 31.32 seconds |
| `python -m compileall src tests` | Passed |
| `python scripts/check_secret_hygiene.py` | Passed for tracked files, including newly committed notification modules |
| `npm ci` in frontend | Passed; 94 packages installed |
| `npm run build` in frontend | Passed; TypeScript and Vite |
| `npm test` in frontend | 11 passed |

Coverage is statement coverage measured by pytest-cov: 91.55% overall backend,
95.83% NotificationRouter, 90.38% Composio notification adapters, 93.37% WatchService.
No claim of 100% coverage or of passing unexecuted hosted deployment checks is made.

The remote feature branch had advanced by 21 commits during this task. Those
OKX-only and freshness fixes were retained by rebasing on
`8da009c4a074c5e735e49819357cb69c49945682`; no merge commit was created.

## Actual Native delivery E2E evidence

A local NotificationRouter process requested its operations through a Native
bridge. Each operation was executed by the authorized hosted Composio tools and
its raw action response returned to the process. These were actual writes, not
receipt fixtures. The message was visibly labelled TEST ONLY / PAPER ONLY / LIVE
EXECUTION OFF and contained no real-market recommendation.

- signal_id: `native-e2e-20261008-1659-router-v1`
- event_id: `50c46f35c090080fce4c25b027213c281c6b5067e182fb1a0325971376079381`
- created_at_ms: `1791478965035`
- ChatGPT Push: DELIVERY_FAILED, `channel_unbound`, no message sent.
- Composio Email: SENT, authenticated user's mailbox only,
  message_id `1a11c78e92e64253`, latency `84456` ms.
- Composio Telegram: SENT, account `ugolovka`, bot `@Artur01rus_bot`,
  chat_id `8999343417`, `data.ok=true`, positive message_id `6`, latency `84461` ms.
- Overall event: **DELIVERY_FAILED**, retry_count `0`. Correctly not SENT.
- A second router process using the same durable journal and signal_id returned
  the same event/receipts without issuing any further external requests.

The latencies include Native tool discovery, read-only preflight, orchestration
round trips and waiting for bridge responses. The diagnostic router timeout was
180 seconds per channel; the normal application timeout is 20 seconds. These
measurements are not production latency estimates or end-user read receipts.
Telegram message_id proves the Bot API accepted the message into the exact chat;
Gmail message_id proves send acceptance, not that the user opened an email.

## Full E2E blocker and next steps

Composio discovery and its fallback tool search produced no native ChatGPT Push
sender with an acknowledgement receipt. Pushbullet/Pushover/Pushinator tools are
different products and were not substituted, connected or used. Consequently the
requested real Watch→Router→ChatGPT Push→Email→Telegram all-success E2E is **not
passed**. The automated full pipeline test uses deterministic market fixtures
and doubles, and the actual Native delivery test starts at the Router. No real
HIGH candidate was fabricated to claim complete external acceptance.

The authorized Native host must bind a real ChatGPT Push receipt transport and the
Composio executor described in `CRYPTO_SIGNAL_NOTIFICATION_ROUTER.md`. Then run a
new explicitly labelled E2E event with all three real channels; verify each receipt
and the aggregate SENT state. Existing SENT/DELIVERY_UNCERTAIN/failed test IDs must
not be replayed. The separately deployed preview is not switched by this change.
