# V4.3 discovery audit and setup selection

Problem: the first valid WATCH in model order can hide another valid setup on the
same coin with better entry quality. V4.3 ranks all valid patterns using the existing
READY/quality/net-reward/proximity ordering before selecting one. It never moves
an active episode's stop or first target to manufacture a better ratio.

The V4.2 >=1 net reward/risk and aligned fresh hourly flow gates remain unchanged.
No new high/low, volume, liquidity or early-entry threshold is promoted to production.

An observation-only early-turn cohort captures a defined 5m setup when verified
15m structure and closed 15m taker pressure/price align but hourly structure has not
turned. LONG and SHORT are mirrored. All existing history, proximity, chase, first
obstacle and positive-net-room checks remain. Records are EARLY_OBSERVATION and
cannot create episodes or send Telegram alerts. Their 15/60/180-minute outcomes
are direction observations, never assumed executed trades or user PnL.

Fresh 15m/30m flow windows, closed 15m/30m/60m returns and location relative to the
last 24 closed hourly candles are recorded. Range location is descriptive, not an
oversold/overbought veto; values may exceed 0–100 after a breakout. Telegram can
explain a shorter-window recovery while hourly confirmation is still absent.

Audit logs expose each considered coin's structure, setup failures, geometry,
quality blockers and prior movement every five minutes, in bounded eight-row batches.
New-version recent outcomes receive 60 of the existing 90-row resolution budget;
the remaining budget continues historical work. Old missing archives cannot consume
the entire queue. Early observations retain their candle feed for the 3h outcome path.

The existing one-hour per-coin cooldown now survives version changes, preventing
deployment-induced re-notification. Active episodes still close via the existing
strategy-change mechanism on release; history and source data remain intact.

Validation: 97 chart tests, including mirrored better-WATCH selection, early-turn
data/flow guards, an enabled-runtime test proving observations never publish or
create episodes, fair outcome resolution, retained observation feeds and cooldown
across versions. Launcher identity and signal-only boot are checked separately.
Performance remains unvalidated; no tuned win-rate or profit promise is made.

Release command: `python -u long_short_v43_stable.py`.
Rollback: pin `8ba6aa411c170ca482d885f4a5b01a4dc9e13d19` on
`release/chart-v42-quality-20261024` and restore `python -u long_short_v42_stable.py`.
