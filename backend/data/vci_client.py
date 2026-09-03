"""Direct VCI (Vietcap) API client — no vnstock dependency."""
from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://trading.vietcap.com.vn/",
    "Origin": "https://trading.vietcap.com.vn",
}

_GRAPHQL_URL = "https://trading.vietcap.com.vn/data-mt/graphql"
_OHLC_URL = "https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart"
_IQ_BASE_URL = "https://iq.vietcap.com.vn/api/iq-insight-service/v1/company"

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
    public_date: Optional[str] = None
    source_created_at: Optional[str] = None
    source_updated_at: Optional[str] = None
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
        self._lock = threading.Lock()

    def _throttle(self) -> None:
        """Thread-safe rate limiter that staggers concurrent callers."""
        with self._lock:
            now = time.time()
            next_allowed = self._last_request + self._min_interval
            if now < next_allowed:
                wait_time = next_allowed - now
            else:
                wait_time = 0.0
            # Advance slot so the next thread gets the subsequent slot
            self._last_request = max(now, next_allowed)
        if wait_time > 0:
            time.sleep(wait_time)

    def _post_json(self, url: str, payload: dict, *, _retries: int = 3) -> dict:
        last_error: Exception | None = None
        for attempt in range(_retries):
            self._throttle()
            try:
                resp = self._client.post(url, json=payload)
            except httpx.TransportError as exc:
                last_error = exc
                if attempt + 1 == _retries:
                    raise
                time.sleep(2 ** attempt + random.uniform(0, 1))
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = float(resp.headers.get("Retry-After", 5))
                backoff = retry_after * (2 ** attempt) + random.uniform(0, 1)
                log.warning(
                    "VCI HTTP %d on attempt %d, backing off %.1fs",
                    resp.status_code, attempt + 1, backoff,
                )
                if attempt + 1 == _retries:
                    resp.raise_for_status()
                time.sleep(backoff)
                continue
            resp.raise_for_status()
            return resp.json()
        if last_error:
            raise last_error
        raise RuntimeError("VCI request failed without a response")

    def _get_json(
        self, url: str, params: dict | None = None, *, retries: int = 3
    ) -> dict:
        """GET JSON with the same throttling/backoff used by price requests."""
        last_error: Exception | None = None
        for attempt in range(retries):
            self._throttle()
            try:
                resp = self._client.get(url, params=params)
            except httpx.TransportError as exc:
                last_error = exc
                if attempt + 1 == retries:
                    raise
                time.sleep(2 ** attempt + random.uniform(0, 1))
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = float(resp.headers.get("Retry-After", 5)) * (2 ** attempt)
                if attempt + 1 == retries:
                    resp.raise_for_status()
                time.sleep(wait + random.uniform(0, 1))
                continue
            resp.raise_for_status()
            return resp.json()
        if last_error:
            raise last_error
        raise RuntimeError("VCI request failed without a response")

    def get_financial_ratios(
        self, symbol: str, period: str = "Y"
    ) -> list[VCIFinancialRow]:
        """Fetch annual or quarterly rows from Vietcap's maintained IQ REST API."""
        statement, statistics = self._get_financial_payload(symbol)
        return self._parse_financial_rows(
            symbol, statement, statistics, period=period
        )

    def get_all_financial_ratios(self, symbol: str) -> list[VCIFinancialRow]:
        """Fetch annual and quarterly ratios with one pair of IQ requests."""
        statement, statistics = self._get_financial_payload(symbol)
        return (
            self._parse_financial_rows(
                symbol, statement, statistics, period="Y"
            )
            + self._parse_financial_rows(
                symbol, statement, statistics, period="Q"
            )
        )

    def _get_financial_payload(
        self, symbol: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        sym = symbol.upper()
        base = f"{_IQ_BASE_URL}/{sym}"
        try:
            statement = self._get_json(
                f"{base}/financial-statement",
                {"section": "INCOME_STATEMENT"},
            ).get("data") or {}
            statistics = (
                self._get_json(f"{base}/statistics-financial").get("data") or []
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return {}, []
            raise
        return statement, statistics

    @staticmethod
    def _parse_financial_rows(
        symbol: str,
        statement: dict[str, Any],
        statistics: list[dict[str, Any]],
        *,
        period: str,
    ) -> list[VCIFinancialRow]:
        sym = symbol.upper()
        period_key = "years" if period == "Y" else "quarters"
        stats_map: dict[tuple[int, int], dict] = {}
        for item in statistics:
            try:
                stats_map[(int(item["year"]), int(item["quarter"]))] = item
            except (KeyError, TypeError, ValueError):
                continue

        result: list[VCIFinancialRow] = []
        for row in statement.get(period_key) or []:
            try:
                year = int(row["yearReport"])
                report_length = int(row.get("lengthReport") or 5)
            except (KeyError, TypeError, ValueError):
                continue
            quarter = report_length if period == "Q" and report_length < 5 else None
            stats = stats_map.get((year, report_length), {})
            shares = _number(stats.get("numberOfSharesMktCap"))
            eps = _number(row.get("isa23"))
            if (eps is None or eps == 0) and shares:
                owner_profit = _number(row.get("isa22"))
                eps = owner_profit / shares if owner_profit is not None else None
            market_cap = _number(stats.get("marketCap"))

            result.append(VCIFinancialRow(
                symbol=sym,
                year=year,
                quarter=quarter,
                public_date=_iso_date(row.get("publicDate")),
                source_created_at=_iso_timestamp(row.get("createDate")),
                source_updated_at=_iso_timestamp(row.get("updateDate")),
                eps=_dec(eps),
                pe=_dec(stats.get("pe")),
                pb=_dec(stats.get("pb")),
                roe=_dec(stats.get("roe")),
                revenue=_dec(row.get("isa3") or row.get("isa1")),
                net_profit=_dec(row.get("isa22") or row.get("isa20")),
                bvps=None,
                # The strategy database stores both values in raw units even
                # though the legacy column names contain "_millions" and
                # "_billions". Keep VND/shares here to match historical rows
                # and the market-cap filter's VND thresholds.
                issue_share=_dec(shares),
                ev=_dec(market_cap),
            ))
        return result

    def get_annual_ratios(self, symbol: str) -> list[VCIFinancialRow]:
        return self.get_financial_ratios(symbol, "Y")

    def get_quarterly_ratios(self, symbol: str) -> list[VCIFinancialRow]:
        return self.get_financial_ratios(symbol, "Q")

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


def _number(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _iso_timestamp(value: Any) -> str | None:
    """Normalize a Vietcap source timestamp without inventing missing dates."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        # Vietcap uses ISO-8601 without a timezone. SQLite compares this
        # normalized representation deterministically.
        return text.replace(" ", "T")
    except (TypeError, ValueError):
        return None


def _iso_date(value: Any) -> str | None:
    timestamp = _iso_timestamp(value)
    return timestamp[:10] if timestamp and len(timestamp) >= 10 else None
