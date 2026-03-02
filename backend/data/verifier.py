"""Cross-check VCI vs KBS financial data for reliability verification."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Optional

from .kbs_client import KBSClient, KBSFinancialRow
from .vci_client import VCIClient, VCIFinancialRow


class Status(str, Enum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
    MISSING = "missing"


@dataclass
class MetricComparison:
    metric: str
    vci_value: Optional[Decimal]
    kbs_value: Optional[Decimal]
    diff_pct: Optional[float]
    status: Status
    note: str = ""


@dataclass
class VerificationResult:
    symbol: str
    year: int
    comparisons: list[MetricComparison]
    overall_status: Status

    @property
    def ok_count(self) -> int:
        return sum(1 for c in self.comparisons if c.status == Status.OK)

    @property
    def warning_count(self) -> int:
        return sum(1 for c in self.comparisons if c.status == Status.WARNING)

    @property
    def error_count(self) -> int:
        return sum(1 for c in self.comparisons if c.status == Status.ERROR)


# Thresholds for discrepancy flagging
_THRESHOLDS: dict[str, float] = {
    "eps": 5.0,
    "pe": 10.0,
    "pb": 10.0,
    "roe": 10.0,
    "revenue": 5.0,
    "net_profit": 5.0,
    "market_cap": 10.0,
    "bvps": 10.0,
}


def _get_vci_val(
    vci: Optional[VCIFinancialRow], metric: str
) -> Optional[Decimal]:
    """Get metric value from VCI row with normalization."""
    if vci is None:
        return None
    if metric == "market_cap":
        return None  # VCI doesn't have market_cap directly
    val = getattr(vci, metric, None)
    if val is None:
        return None
    # VCI ROE is decimal (0.30 = 30%); KBS is percentage (30.0)
    if metric == "roe" and abs(val) < Decimal("1"):
        val = val * 100
    return val


def verify_symbol(
    symbol: str,
    vci: VCIClient,
    kbs: KBSClient,
    year: Optional[int] = None,
) -> VerificationResult:
    """Compare VCI vs KBS financial data for a single symbol."""
    vci_rows = vci.get_annual_ratios(symbol)
    kbs_row = kbs.get_financial_summary(symbol)

    if year is None:
        year = kbs_row.year

    # Find matching VCI year
    vci_match: Optional[VCIFinancialRow] = None
    for r in vci_rows:
        if r.year == year and r.quarter is None:
            vci_match = r
            break

    comparisons: list[MetricComparison] = []
    for metric, threshold in _THRESHOLDS.items():
        vci_val = _get_vci_val(vci_match, metric)
        kbs_val = getattr(kbs_row, metric, None)
        comp = _compare(metric, vci_val, kbs_val, threshold)
        comparisons.append(comp)

    worst = Status.OK
    for c in comparisons:
        if c.status == Status.ERROR:
            worst = Status.ERROR
            break
        if c.status == Status.WARNING and worst != Status.ERROR:
            worst = Status.WARNING

    return VerificationResult(
        symbol=symbol.upper(), year=year,
        comparisons=comparisons, overall_status=worst,
    )


def verify_batch(
    symbols: list[str],
    vci: VCIClient,
    kbs: KBSClient,
    year: Optional[int] = None,
) -> list[VerificationResult]:
    """Verify multiple symbols. Returns list of results."""
    results: list[VerificationResult] = []
    for s in symbols:
        try:
            results.append(verify_symbol(s, vci, kbs, year))
        except Exception as e:
            results.append(VerificationResult(
                symbol=s.upper(), year=year or 0,
                comparisons=[MetricComparison(
                    metric="fetch_error", vci_value=None, kbs_value=None,
                    diff_pct=None, status=Status.ERROR, note=str(e),
                )],
                overall_status=Status.ERROR,
            ))
    return results


def _compare(
    metric: str, vci: Optional[Decimal], kbs: Optional[Decimal], threshold: float
) -> MetricComparison:
    if vci is None and kbs is None:
        return MetricComparison(metric, None, None, None, Status.MISSING, "both missing")
    if vci is None:
        return MetricComparison(metric, None, kbs, None, Status.WARNING, "VCI missing")
    if kbs is None:
        return MetricComparison(metric, vci, None, None, Status.WARNING, "KBS missing")
    if vci == 0 and kbs == 0:
        return MetricComparison(metric, vci, kbs, 0.0, Status.OK)
    base = max(abs(vci), abs(kbs))
    if base == 0:
        return MetricComparison(metric, vci, kbs, None, Status.WARNING, "zero base")
    diff = float(abs(vci - kbs) / base * 100)
    status = Status.OK if diff <= threshold else (
        Status.WARNING if diff <= threshold * 2 else Status.ERROR
    )
    return MetricComparison(metric, vci, kbs, round(diff, 2), status)
