"""Direct VCI (Vietcap) API client — no vnstock dependency."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

import httpx

_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://trading.vietcap.com.vn/",
    "Origin": "https://trading.vietcap.com.vn",
}

_GRAPHQL_URL = "https://trading.vietcap.com.vn/data-mt/graphql"
_OHLC_URL = "https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart"

# Exact payload format from VCI API (fragment-based with all field codes).
# This is the proven format used by vnstock — simplified queries return 400.
_RATIO_PAYLOAD_TEMPLATE = json.loads(
    '{"query":"fragment Ratios on CompanyFinancialRatio {\\n  ticker\\n'
    "  yearReport\\n  lengthReport\\n  updateDate\\n  revenue\\n"
    "  revenueGrowth\\n  netProfit\\n  netProfitGrowth\\n  ebitMargin\\n"
    "  roe\\n  roic\\n  roa\\n  pe\\n  pb\\n  eps\\n  currentRatio\\n"
    "  cashRatio\\n  quickRatio\\n  interestCoverage\\n  ae\\n"
    "  netProfitMargin\\n  grossMargin\\n  ev\\n  issueShare\\n  ps\\n"
    "  pcf\\n  bvps\\n  evPerEbitda\\n  BSA1\\n  BSA2\\n  BSA5\\n"
    "  BSA8\\n  BSA10\\n  BSA159\\n  BSA16\\n  BSA22\\n  BSA23\\n"
    "  BSA24\\n  BSA162\\n  BSA27\\n  BSA29\\n  BSA43\\n  BSA46\\n"
    "  BSA50\\n  BSA209\\n  BSA53\\n  BSA54\\n  BSA55\\n  BSA56\\n"
    "  BSA58\\n  BSA67\\n  BSA71\\n  BSA173\\n  BSA78\\n  BSA79\\n"
    "  BSA80\\n  BSA175\\n  BSA86\\n  BSA90\\n  BSA96\\n  CFA21\\n"
    "  CFA22\\n  at\\n  fat\\n  acp\\n  dso\\n  dpo\\n  ccc\\n  de\\n"
    "  le\\n  ebitda\\n  ebit\\n  dividend\\n  RTQ10\\n"
    "  charterCapitalRatio\\n  RTQ4\\n  epsTTM\\n  charterCapital\\n"
    "  fae\\n  RTQ17\\n  CFA26\\n  CFA6\\n  CFA9\\n  BSA85\\n"
    "  CFA36\\n  BSB98\\n  BSB101\\n  BSA89\\n  CFA34\\n  CFA14\\n"
    "  ISB34\\n  ISB27\\n  ISA23\\n  ISS152\\n  ISA102\\n  CFA27\\n"
    "  CFA12\\n  CFA28\\n  BSA18\\n  BSB102\\n  BSB110\\n  BSB108\\n"
    "  CFA23\\n  ISB41\\n  BSB103\\n  BSA40\\n  BSB99\\n  CFA16\\n"
    "  CFA18\\n  CFA3\\n  ISB30\\n  BSA33\\n  ISB29\\n  CFS200\\n"
    "  ISA2\\n  CFA24\\n  BSB105\\n  CFA37\\n  ISS141\\n  BSA95\\n"
    "  CFA10\\n  ISA4\\n  BSA82\\n  CFA25\\n  BSB111\\n  ISI64\\n"
    "  BSB117\\n  ISA20\\n  CFA19\\n  ISA6\\n  ISA3\\n  BSB100\\n"
    "  ISB31\\n  ISB38\\n  ISB26\\n  BSA210\\n  CFA20\\n  CFA35\\n"
    "  ISA17\\n  ISS148\\n  BSB115\\n  ISA9\\n  CFA4\\n  ISA7\\n"
    "  CFA5\\n  ISA22\\n  CFA8\\n  CFA33\\n  CFA29\\n  BSA30\\n"
    "  BSA84\\n  BSA44\\n  BSB107\\n  ISB37\\n  ISA8\\n  BSB109\\n"
    "  ISA19\\n  ISB36\\n  ISA13\\n  ISA1\\n  BSB121\\n  ISA14\\n"
    "  BSB112\\n  ISA21\\n  ISA10\\n  CFA11\\n  ISA12\\n  BSA15\\n"
    "  BSB104\\n  BSA92\\n  BSB106\\n  BSA94\\n  ISA18\\n  CFA17\\n"
    "  ISI87\\n  BSB114\\n  ISA15\\n  BSB116\\n  ISB28\\n  BSB97\\n"
    "  CFA15\\n  ISA11\\n  ISB33\\n  BSA47\\n  ISB40\\n  ISB39\\n"
    "  CFA7\\n  CFA13\\n  ISS146\\n  ISB25\\n  BSA45\\n  BSB118\\n"
    "  CFA1\\n  CFS191\\n  ISB35\\n  CFB65\\n  CFA31\\n  BSB113\\n"
    "  ISB32\\n  ISA16\\n  CFS210\\n  BSA48\\n  BSA36\\n  ISI97\\n"
    "  CFA30\\n  CFA2\\n  CFB80\\n  CFA38\\n  CFA32\\n  ISA5\\n"
    "  BSA49\\n  CFB64\\n  __typename\\n}\\n\\nquery Query($ticker:"
    ' String!, $period: String!) {\\n  CompanyFinancialRatio(ticker:'
    " $ticker, period: $period) {\\n    ratio {\\n      ...Ratios\\n"
    '      __typename\\n    }\\n    period\\n    __typename\\n  }\\n}\\n",'
    '"variables":{"ticker":"_","period":"Y"}}'
)


@dataclass
class VCIFinancialRow:
    symbol: str
    year: int
    quarter: Optional[int]
    eps: Optional[Decimal] = None
    pe: Optional[Decimal] = None
    pb: Optional[Decimal] = None
    roe: Optional[Decimal] = None
    revenue: Optional[Decimal] = None
    net_profit: Optional[Decimal] = None
    bvps: Optional[Decimal] = None
    issue_share: Optional[Decimal] = None
    ev: Optional[Decimal] = None


class VCIClient:
    """Direct VCI API client with rate limiting."""

    def __init__(self, rate_limit_rpm: int = 30):
        self._client = httpx.Client(headers=_HEADERS, timeout=30.0)
        self._min_interval = 60.0 / max(rate_limit_rpm, 1)
        self._last_request = 0.0

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request = time.time()

    def _post_json(self, url: str, payload: dict) -> dict:
        self._throttle()
        resp = self._client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()

    def get_financial_ratios(
        self, symbol: str, period: str = "Y"
    ) -> list[VCIFinancialRow]:
        """Fetch financial ratios. period='Y' annual, 'Q' quarterly."""
        payload = json.loads(json.dumps(_RATIO_PAYLOAD_TEMPLATE))
        payload["variables"]["ticker"] = symbol.upper()
        payload["variables"]["period"] = period
        data = self._post_json(_GRAPHQL_URL, payload)
        if "errors" in data:
            raise RuntimeError(f"VCI GraphQL error: {data['errors']}")
        rows_raw = (
            data.get("data", {})
            .get("CompanyFinancialRatio", {})
            .get("ratio") or []
        )
        result: list[VCIFinancialRow] = []
        for r in rows_raw:
            year = r.get("yearReport")
            if year is None:
                continue
            length = r.get("lengthReport")
            quarter = int(length) if length and period == "Q" else None
            result.append(VCIFinancialRow(
                symbol=symbol.upper(),
                year=int(year),
                quarter=quarter,
                eps=_dec(r.get("eps")),
                pe=_dec(r.get("pe")),
                pb=_dec(r.get("pb")),
                roe=_dec(r.get("roe")),
                revenue=_dec(r.get("revenue")),
                net_profit=_dec(r.get("netProfit")),
                bvps=_dec(r.get("bvps")),
                issue_share=_dec(r.get("issueShare")),
                ev=_dec(r.get("ev")),
            ))
        return result

    def get_annual_ratios(self, symbol: str) -> list[VCIFinancialRow]:
        return self.get_financial_ratios(symbol, "Y")

    def get_ohlcv(
        self, symbol: str, count_back: int = 60
    ) -> list[dict[str, Any]]:
        """Fetch recent OHLCV bars (daily)."""
        now_ts = int(time.time())
        payload = {
            "timeFrame": "ONE_DAY",
            "symbols": [symbol.upper()],
            "to": now_ts,
            "countBack": count_back,
        }
        data = self._post_json(_OHLC_URL, payload)
        if not data or not isinstance(data, list):
            return []
        bars = data[0] if data else {}
        times = bars.get("t", [])
        opens = bars.get("o", [])
        highs = bars.get("h", [])
        lows = bars.get("l", [])
        closes = bars.get("c", [])
        volumes = bars.get("v", [])
        return [
            {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v}
            for t, o, h, l, c, v in zip(times, opens, highs, lows, closes, volumes)
            if _to_int(t) is not None
        ]

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> VCIClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def _to_int(val: Any) -> int | None:
    """Coerce VCI timestamp to int (API sometimes returns string digits).

    Returns None instead of 0 for unparseable values to prevent
    silent insertion of 1970-01-01 bars.
    """
    if isinstance(val, int):
        return val
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _dec(val: Any) -> Optional[Decimal]:
    if val is None:
        return None
    try:
        return Decimal(str(val))
    except Exception:
        return None
