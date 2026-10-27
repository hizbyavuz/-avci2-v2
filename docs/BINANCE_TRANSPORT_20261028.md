# Binance transport repair, 28 October 2026

The running worker repeatedly lost the two 240-symbol candle sockets within
seconds, while its 38-symbol socket stayed connected. Ticker and contract
updates shared the first failing socket, so loss of that socket also forced
metadata/ticker/time reads back to REST. Old logs recorded only the exception
class, so they do not establish the exchange's reason for disconnecting.

This revision puts ticker/contract events on a dedicated control socket and
subscribes to at most 60 symbols (180 candle streams) per candle socket. All
eligible symbols remain covered. Subscriptions use numeric IDs and validate
their acknowledgements. Logs distinguish connection establishment from ACK,
and record frame size, close codes, HTTP status and uptime without response
bodies or IP addresses. The existing shared connection pacing and exponential
reconnect backoff remain in place.

REST weight headers are now monotonic within a minute. Missing, malformed or
lower headers cannot erase observed usage. Requests still consume an estimated
budget when the next response omits its header, and error responses contribute
headers too. Cooldown logs include endpoint and local-versus-IP usage evidence.

The observed local-versus-IP weight discrepancy does not identify another user
of the IP. Neither shared egress nor oversized subscriptions is asserted as the
proven cause of the exchange ban. The subscription stream counts were below
Binance's documented maximum even before this repair.

The strategy configuration, core logic, Bybit venue separation, order safeguards,
persisted exchange ban and user retry floor are unchanged. No alternate host,
proxy, IP rotation or early retry is introduced. The observed exchange deadline
remains 1793212682232 (28 October 2026 21:38:02.232 Europe/Istanbul); a deployment
must preserve this value. Live Binance transport cannot be validated while that
deadline is active. The deployment should first show zero Binance requests and
the same stored deadline, with Bybit continuing independently.

Validation: `python -m unittest discover -s tests -p 'test_ta_paper*.py'`.
Coverage includes a local WebSocket handshake and closed-bar ingestion,
full-universe sharding, control/candle isolation, ACK validation, frame size,
missing/error response headers, budget exhaustion, persisted cooldowns and
unchanged assessments. An existing retry-display test now also freezes time
during its final historical-date assertion.
