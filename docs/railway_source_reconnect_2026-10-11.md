# Railway GitHub source reconnection — 2026-10-11

Production service: `long-short-motor` in `long-short-analyst`.
Repository: `hizbyavuz/-avci2-v2`, branch: `main`.

The GitHub Railway App authorization was restored and the service's auto-deploy option enabled by the account owner. The prior running deployment was pinned to `3cc47628bfd9b732c385b3c667526f6c0d1aaa5c`. This documentation-only commit creates a fresh `main` push to request a deployment of the already-merged, CI-tested changes from PR #68. It does not alter runtime thresholds, execution authorization, trading behavior, or environment variables.

Verification required: Railway should report a new deployment built from this commit, status SUCCESS, and subsequent logs should show normal scan/health behavior. If it does not, do not report production rollout as complete. Keep automatic trading disabled.
