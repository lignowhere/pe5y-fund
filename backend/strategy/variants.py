"""Shared strategy variant definitions."""
from __future__ import annotations

STRATEGY_PARAMS = {
    "TTM_20Q": {
        "max_quarters": 20,
        "require_all_positive": False,
        "require_last_n_positive": 0,
    },
    "LAST_8Q_PLUS": {
        # Original semantics: value stocks on the same trailing window as
        # TTM_20Q, then require the eight most recent quarters to be profitable.
        "max_quarters": 20,
        "require_all_positive": False,
        "require_last_n_positive": 8,
    },
}


def signal_params(strategy: str) -> dict[str, object]:
    """Parameters accepted by ``generate_signal_20q`` for one variant."""
    return {
        key: value
        for key, value in STRATEGY_PARAMS[strategy].items()
        if key != "max_quarters"
    }
