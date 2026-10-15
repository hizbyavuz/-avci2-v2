# Opportunity notices and coherent local invalidation

User authorized two distinct notification meanings: observation/opportunity and
entry conditions completed. Opportunities do not authorize an order, entry event,
paper fill, or bypass any live execution gate. The existing early observer remains
a research input. New notices consume recent native analyst observations with a
known direction, existing score/edge and liquidity/volatility gates, fresh native
price and verified 4/4 or exactly 3/4 derivatives. Missing fields are named.

One initial opportunity per symbol episode; at most three new notices per scan.
Repeated same-direction observations do not send updates. Invalidated, expired or
direction-lost episodes edit the existing message. A same-direction repeat has a
one-hour cooldown. Unreviewed episodes expire after 45 minutes; maximum episode is
three hours. Initial-send uncertainty is persisted and never blindly retried.
Opportunity-to-trade promotion gets a new notification, exactly once, so a real
entry is not a silent edit. Active trade cards cannot be overwritten by a watch.
Existing destination resolution and expected-chat checks remain in effect.

V6 uses the latest intact opposite pivot from the last 48 closed 5m bars (two bars
on either side to confirm it) as the local invalidation. It must be on the proper
side of current price and the planned retest. No valid pivot means no executable
plan. It is not selected to achieve a desired R value. The existing zone-based
entry/target, costs, room and net-R thresholds remain. V5 is computed on the same
candles and preserved as a separate comparison; the strategy identity changes.
This is an authorized model change, not proof of predictive accuracy/profitability.

Notifications say FIRSATI / Giriş bekleniyor for observations and Giriş koşulları
oluştu for confirmed entries. Opportunity prices and invalidation are labeled as
observations, not entry/SL/TP instructions. No live automatic orders are enabled.

Logs: STRUCTURAL_PLAN_V6, OPPORTUNITY_NOTICE_SUMMARY, TELEGRAM_CARD_ACK with source
OPPORTUNITY. State: opportunity_notices_v1 in the existing persistent observation
database, plus existing cards/deliveries records for Telegram receipts. Disable
only the new notices with LS_OPPORTUNITY_NOTICES_V1=0. Rollback code source to the
prior pinned commit to restore V5. Old strategy outcomes are not relabeled.
