"""Publication eligibility is separate from a chart setup's READY state.

No model is validated for trading. The existing LONG may produce explicitly
labelled research notices; failed SHORT models stay measurable, never published
as current recommendations. An environment flag cannot change this evidence.
"""
from long_short_swing import VERSION as SWING_VERSION, CONFIG_HASH as SWING_HASH
from long_short_short_retest import VERSION as SHORT_VERSION, CONFIG_HASH as SHORT_HASH

VERSION = 'SIGNAL_PUBLICATION_POLICY_V1_2026_10_22'
MODELS = {
    'BASE_BREAKOUT_LONG': (SWING_VERSION, SWING_HASH, 'LONG', 'RESEARCH_ONLY'),
    'FAILED_BOUNCE_SHORT': (SWING_VERSION, SWING_HASH, 'SHORT', 'REJECTED'),
    'FAILED_BREAKOUT_SHORT': (SHORT_VERSION, SHORT_HASH, 'SHORT', 'REJECTED'),
    'BEAR_RETEST_SHORT': (SHORT_VERSION, SHORT_HASH, 'SHORT', 'REJECTED'),
}


def publication_decision(candidate):
    out = dict(policy_version=VERSION, research_publish_allowed=False,
               actionable=False, win_probability=None, status='UNKNOWN',
               reason='MODEL_NOT_REVIEWED')
    model = MODELS.get(candidate.get('model'))
    if not model: return out
    version, config_hash, direction, status = model
    out['status'] = status
    if (candidate.get('version'), candidate.get('config_hash'), candidate.get('direction')) != (version, config_hash, direction):
        return dict(out, reason='MODEL_IDENTITY_MISMATCH')
    if status == 'REJECTED':
        return dict(out, reason='SHORT_MODEL_FAILED_RESEARCH')
    if candidate.get('stage') != 'READY':
        return dict(out, reason='CANDIDATE_NOT_READY')
    if candidate.get('research_only') is not True or candidate.get('actionable') is True:
        return dict(out, reason='UNSUPPORTED_TRADING_CLAIM')
    return dict(out, reason='LONG_RESEARCH_ONLY_NOT_VALIDATED', research_publish_allowed=True)


def status():
    return dict(version=VERSION, trading_validated=False,
                models={name: dict(direction=spec[2], status=spec[3], actionable=False)
                        for name, spec in MODELS.items()})


def require_explicit_profile(environment):
    """This research release must not silently boot the retired MAJOR sender."""
    if environment.get('LS_RUNTIME_PROFILE') != 'SWING_STRUCTURE_V1':
        raise RuntimeError('EXPLICIT_SWING_PROFILE_REQUIRED_NO_LEGACY_FALLBACK')
