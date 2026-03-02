"""Direct KBS (KB Securities Vietnam) API client."""
from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

import httpx

_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

_PROFILE_URL = "https://kbbuddywts.kbsec.com.vn/iis-server/investment/stockinfo/profile"
_FINANCE_URL = "https://kbbuddywts.kbsec.com.vn/sas/kbsv-stock-data-store/stock/finance-info"
_OHLC_URL = "https://kbbuddywts.kbsec.com.vn/sas/kbsv-stock-data-store/stock/ohlc-chart"

# Map Vietnamese metric names → our field names
_RATIO_NAME_MAP = {
    "Thu nhập trên mỗi cổ phần của 4 quý gần nhất (EPS)": "eps",
    "Chỉ số giá thị trường trên thu nhập (P/E)": "pe",
    "Chỉ số giá thị trường trên giá trị sổ sách (P/B)": "pb",
    "Giá trị sổ sách của cổ phiếu (BVPS)": "bvps",
    "Tỷ suất lợi nhuận trên vốn chủ sở hữu bình quân (ROEA)": "roe",
    "Tỷ suất sinh lợi trên tổng tài sản bình quân (ROAA)": "roa",
}

# Income statement items we care about (prefix-match)
_INCOME_ITEMS = {
    "1. Doanh thu bán hàng": "revenue",
    "18. Lợi nhuận sau thuế thu nhập doanh nghiệp": "net_profit",
    "19. Lãi cơ bản trên cổ phiếu": "eps_basic",
}


@dataclass
class KBSFinancialRow:
    symbol: str
    year: int
    quarter: Optional[int] = None
    eps: Optional[Decimal] = None
    pe: Optional[Decimal] = None
    pb: Optional[Decimal] = None
    roe: Optional[Decimal] = None
    revenue: Optional[Decimal] = None
    net_profit: Optional[Decimal] = None
    bvps: Optional[Decimal] = None


class KBSClient:
    """Direct KBS API client with rate limiting."""

    def __init__(self, rate_limit_rpm: int = 30):
        self._client = httpx.Client(headers=_HEADERS, timeout=30.0)
        self._min_interval = 60.0 / max(rate_limit_rpm, 1)
        self._last_request = 0.0

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request = time.time()

    def _get(self, url: str, params: dict) -> dict:
        self._throttle()
        resp = self._client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def get_stock_profile(self, symbol: str) -> dict[str, Any]:
        """Fetch company profile (basic info, not financials)."""
        return self._get(f"{_PROFILE_URL}/{symbol.upper()}", {"l": "1"})

    def get_financial_summary(self, symbol: str) -> KBSFinancialRow:
        """Fetch financial ratios + income statement for latest year."""
        sym = symbol.upper()
        # Fetch ratios (CSTC) and income statement (KQKD)
        base_params = {"page": 1, "pageSize": 2, "unit": 1,
                       "termtype": 1, "languageid": 1}
        ratios = self._get(f"{_FINANCE_URL}/{sym}", {**base_params, "type": "CSTC"})
        income = self._get(f"{_FINANCE_URL}/{sym}", {**base_params, "type": "KQKD"})

        year = _extract_year_from_head(ratios)
        vals: dict[str, Decimal | None] = {}

        # Parse ratio groups
        for items in ratios.get("Content", {}).values():
            for item in items:
                name = item.get("Name", "")
                field = _RATIO_NAME_MAP.get(name)
                if field:
                    vals[field] = _dec(item.get("Value1"))

        # Parse income statement
        for items in income.get("Content", {}).values():
            for item in items:
                name = item.get("Name", "")
                for prefix, field in _INCOME_ITEMS.items():
                    if name.startswith(prefix):
                        vals[field] = _dec(item.get("Value1"))

        return KBSFinancialRow(
            symbol=sym, year=year,
            eps=vals.get("eps"), pe=vals.get("pe"), pb=vals.get("pb"),
            roe=vals.get("roe"), bvps=vals.get("bvps"),
            revenue=vals.get("revenue"), net_profit=vals.get("net_profit"),
        )

    def get_ohlcv(
        self, symbol: str, count_back: int = 30
    ) -> list[dict[str, Any]]:
        """Fetch recent OHLCV bars from KBS as VCI-compatible dicts."""
        sym = symbol.upper()
        params = {"symbol": sym, "timeFrame": "D", "count": count_back}
        data = self._get(_OHLC_URL, params)
        items = data.get("data", [])
        if not items or not isinstance(items, list):
            return []
        bars: list[dict[str, Any]] = []
        for item in items:
            td = item.get("tradingDate", "")
            date_str = td[:10] if td else None
            if not date_str:
                continue
            bars.append({
                "time": date_str,
                "open": item.get("open", 0),
                "high": item.get("high", 0),
                "low": item.get("low", 0),
                "close": item.get("close", 0),
                "volume": item.get("volume", 0),
            })
        return bars

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> KBSClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def _dec(val: Any) -> Optional[Decimal]:
    if val is None:
        return None
    try:
        return Decimal(str(val))
    except Exception:
        return None


def _extract_year_from_head(data: dict) -> int:
    heads = data.get("Head", [])
    if heads:
        return int(heads[0].get("YearPeriod", 0))
    import datetime
    return datetime.date.today().year
