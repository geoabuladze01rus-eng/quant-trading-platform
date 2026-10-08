# Crypto Signal Watch v4 notification delivery

## Contract

All Watch candidates enter `NotificationRouter`. Only confirmed HIGH / VERY HIGH
candidates with healthy, fresh evidence may produce external messages. Rejections
are journaled as NO_SETUP or DATA_BLOCKED without sending. Read-only Watch GETs
return detached cached notification diagnostics and never dispatch messages.

Channels are attempted in this order: `chatgpt_push`, `email`, `telegram`. A channel
that is unavailable is recorded as DELIVERY_FAILED; remaining channels are still
attempted in order. The event is SENT only when all three return confirmed message
identities. Partial success is never reported as successful signal delivery.

The only delivery states are PENDING, SENT, DELIVERY_FAILED, DELIVERY_UNCERTAIN,
SKIPPED, NO_SETUP, DATA_BLOCKED. Events retain event_id, signal_id, created_at,
delivery_status and retry_count in the existing journal SQLite database. Attempts
retain signal_id, channel, message_id, chat_id, delivery_status, sanitized error,
retry_count, integer latency in milliseconds and timestamp in epoch milliseconds.
Financial candidate values remain Decimal strings. No live execution is added.

## Composio Telegram

The adapter fixes account `ugolovka`, chat `8999343417`, and bot username
`Artur01rus_bot`. Alternate accounts/chats are rejected. No direct Bot API,
bot token, private exchange credential, or fallback chat is used.

Before each send the adapter obtains the connection and TELEGRAM_SEND_MESSAGE
schema, checks ACTIVE status and chat_id/text field types, then calls GET_ME,
GET_CHAT and GET_CHAT_MEMBER. Bot identity, exact private chat and membership must
match. `health()` runs these read-only checks and never sends a message. These
preflight checks establish observable access/membership; they cannot guarantee
that permissions or reachability will remain unchanged during the next request.

A send is confirmed only when the Composio response is successful, `data.ok is True`
and `result.message_id` is a positive integer. If a receipt includes chat metadata,
it must match the authorized chat. Missing or invalid identity is DELIVERY_FAILED,
terminal for this signal_id. Evidence is checked again after preflight, immediately
before either Composio Telegram or Email writes.

## Retries and idempotency

A durable reservation is committed before any external effect. signal_id is the
unique event key; duplicate concurrent calls return the reservation. Existing
terminal results are returned unchanged, including SENT and DELIVERY_UNCERTAIN.
A PENDING event found after process recovery becomes DELIVERY_UNCERTAIN and cannot
be replayed. Old `signal_deliveries` reservations are also blocked conservatively;
the previous single-channel sender has been retired, not retained as a bypass.

Each channel permits at most three attempts (two retries), with exponential waits
of 1 and 2 seconds or the provider's larger retry_after. `DeliveryFailure` describes
an explicitly rejected or provably undispatched request. Telegram 429 and 500
require `ok=false` before retry. Verified pre-dispatch timeout/network rejection
may retry. A timeout/network exception after dispatch is ambiguous and becomes
DELIVERY_UNCERTAIN immediately: blindly retrying would violate the no-duplicates
requirement. Missing message_id is terminal and is not replayed either.

The normal per-channel timeout is 20 seconds. The event retry_count is the total
retries across channels; each attempt log has that channel's retry ordinal.

## Native host registration

Before FastAPI lifespan begins, the authorized Native host can supply:

- `app.state.composio_notification_executor`, implementing the typed
  `ComposioExecutor` connection/schema/execute boundary. `connection(account)`
  returns the resolved alias and normalized ACTIVE status, `schema(slug)` returns
  the discovered input schema, and `execute()` returns the single raw Composio
  action response, not the surrounding meta-tool batch.
- `app.state.notification_channels['chatgpt_push']`, an authorized ChatGPT Push
  transport returning `Receipt` only after a real acknowledgement. It must honor
  Event.valid_until_ms immediately before its external write. Displaying a ChatGPT
  response, creating an automation, or calling Pushbullet/Pushover is not proof of
  delivery through ChatGPT Push.

The host executor binds Composio Email and the fixed-recipient Telegram adapter.
Email resolves the authenticated Gmail profile and sends only to that mailbox.
Telegram registrations using another adapter are rejected at application startup.
Without these bindings, channels fail closed as `channel_unbound`; starting the
standalone HTTP service does not create authorized Native sessions automatically.
No existing scheduled workflow or production deployment is changed by this PR.

## Validation and acceptance

See `CRYPTO_SIGNAL_NOTIFICATION_ACCEPTANCE_2026-10-08.md` for commands, coverage,
actual Native delivery receipts and the remaining full E2E blocker. Automated
Watch integration uses deterministic market fixtures and transport doubles; the
Native delivery check uses actual Composio Email and Telegram through the router.
Neither is a claim of a real-market HIGH signal or a successful three-channel E2E.
