"""Build the bounded Data Analytics artifact for the 10-year comparison."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


def _cagr(rows: list[dict[str, Any]]) -> float:
    return math.prod(1 + row["return"] for row in rows) ** (
        1 / len(rows)
    ) - 1


def build(result: dict[str, Any], source_path: str) -> dict[str, Any]:
    ttm = result["strategies"]["TTM_20Q"]
    last8 = result["strategies"]["LAST_8Q_PLUS"]
    vn = result["benchmark"]
    ttm_metrics = ttm["metrics"]
    last8_metrics = last8["metrics"]
    vn_metrics = vn["metrics"]
    full20_ttm = [row for row in ttm["cycles"] if row["quarter_count"] == 20]
    full20_last8 = [
        row for row in last8["cycles"] if row["quarter_count"] == 20
    ]
    ex_peak_ttm = [row for row in ttm["cycles"] if row["hold_year"] != 2020]
    ex_peak_last8 = [
        row for row in last8["cycles"] if row["hold_year"] != 2020
    ]
    beats_ttm = sum(
        left["return"] > right["return"]
        for left, right in zip(ttm["cycles"], vn["cycles"])
    )
    beats_last8 = sum(
        left["return"] > right["return"]
        for left, right in zip(last8["cycles"], vn["cycles"])
    )
    summary = [{
        "ttm_cagr": ttm_metrics["cagr"],
        "ttm_excess_cagr": ttm_metrics["cagr"] - vn_metrics["cagr"],
        "last8_cagr": last8_metrics["cagr"],
        "last8_excess_cagr": last8_metrics["cagr"] - vn_metrics["cagr"],
        "vn_cagr": vn_metrics["cagr"],
        "cagr_gap": last8_metrics["cagr"] - ttm_metrics["cagr"],
        "ttm_volatility": ttm_metrics["annual_volatility"],
        "last8_volatility": last8_metrics["annual_volatility"],
        "volatility_reduction": (
            ttm_metrics["annual_volatility"]
            - last8_metrics["annual_volatility"]
        ),
        "ttm_beats_vn": beats_ttm,
        "last8_beats_vn": beats_last8,
        "full20_ttm_cagr": _cagr(full20_ttm),
        "full20_last8_cagr": _cagr(full20_last8),
        "ex_peak_ttm_cagr": _cagr(ex_peak_ttm),
        "ex_peak_last8_cagr": _cagr(ex_peak_last8),
    }]
    annual_wide = []
    annual_long = []
    for row in result["annual_comparison"]:
        wide = {
            "period": row["period"],
            "quarters": row["quarter_count"],
            "ttm_return": row["ttm20_return_pct"] / 100,
            "last8_return": row["last8q_return_pct"] / 100,
            "vn_return": row["vnindex_return_pct"] / 100,
            "last8_minus_ttm": row["last8_minus_ttm_pp"] / 100,
            "ttm_holdings": row["ttm20_holdings"],
            "last8_holdings": row["last8q_holdings"],
            "overlap": row["overlap_count"],
            "jaccard": row["jaccard_pct"] / 100,
        }
        annual_wide.append(wide)
        for series, field, holdings in (
            ("TTM ≤20Q", "ttm_return", row["ttm20_holdings"]),
            ("LAST 8Q+", "last8_return", row["last8q_holdings"]),
            ("VNINDEX", "vn_return", 1),
        ):
            annual_long.append({
                **wide,
                "series": series,
                "return_rate": wide[field],
                "holdings": holdings,
            })

    annual_values = ",\n        ".join(
        "("
        + ", ".join(
            [
                repr(row["period"]),
                str(row["quarters"]),
                repr(row["ttm_return"]),
                repr(row["last8_return"]),
                repr(row["vn_return"]),
                repr(row["last8_minus_ttm"]),
                str(row["ttm_holdings"]),
                str(row["last8_holdings"]),
                str(row["overlap"]),
            ]
        )
        + ")"
        for row in annual_wide
    )
    source_sql = (
        "WITH annual(period, quarters, ttm_return, last8_return, "
        "vn_return, last8_minus_ttm, ttm_holdings, last8_holdings, "
        "overlap) AS (\n    VALUES\n        "
        + annual_values
        + "\n)\nSELECT * FROM annual ORDER BY period;"
    )
    source = {
        "id": "backtest_10y",
        "label": "Snapshot backtest TTM ≤20Q và LAST 8Q+",
        "path": source_path,
        "query": {
            "engine": "SQLite snapshot; upstream Python 3.12",
            "language": "sql",
            "sql": source_sql,
            "query": (
                "python -m backend.backtest.run_ttm20_vs_last8_10y "
                "--output output/ttm20-vs-last8-10y-min15.json"
            ),
            "description": (
                "Tạo tín hiệu lịch sử, tải giá điều chỉnh Vietcap và mô phỏng "
                "danh mục theo lô, ADV, phí và thuế."
            ),
            "executed_at": result["generated_at"],
            "filters": [
                "Chu kỳ tháng 9/2015 đến tháng 9/2025",
                "Top 10% P/E thấp nhất, tối thiểu 15 mã",
                "Số quý 8/12/16/20 theo dữ liệu sẵn có",
            ],
            "tables_used": [
                "financial_ratios",
                "stock_price_history",
                "financial_data_versions",
                "Vietcap VCI GAP_CHART",
            ],
            "metric_definitions": [
                "CAGR = tích (1 + lợi suất ròng chu kỳ) mũ 1/số chu kỳ - 1.",
                "Lợi suất danh mục gồm tiền mặt dư, làm tròn lô 100, giới hạn "
                "ADV lịch sử, phí môi giới hai chiều và thuế bán.",
                "Giá hiệu suất là giá điều chỉnh Vietcap, phản ánh cổ tức và "
                "chia tách; giá tại ngày chiến lược vẫn dùng để xếp hạng P/E.",
                "Biến động là độ lệch chuẩn tổng thể của 10 lợi suất 12 tháng.",
            ],
        },
    }
    title = "So sánh TTM tối đa 20Q và LAST 8Q+ qua 10 chu kỳ"
    blocks = [
        {"id": "title", "type": "markdown", "body": f"# {title}"},
        {
            "id": "executive_summary",
            "type": "markdown",
            "sourceId": source["id"],
            "body": (
                "## Executive Summary\n\n"
                f"- **Hai chiến lược gần như hòa về tăng trưởng.** CAGR TTM "
                f"≤20Q là **{ttm_metrics['cagr']:.2%}**, LAST 8Q+ là "
                f"**{last8_metrics['cagr']:.2%}**; chênh lệch chỉ "
                f"**{last8_metrics['cagr'] - ttm_metrics['cagr']:+.2%}**.\n"
                f"- **LAST 8Q+ giảm biến động nhưng không thắng ổn định.** "
                f"Biến động năm giảm từ **{ttm_metrics['annual_volatility']:.2%}** "
                f"xuống **{last8_metrics['annual_volatility']:.2%}**; đối đầu "
                f"LAST 8Q+ thắng {result['head_to_head']['last8_wins']}/10, "
                f"TTM thắng {result['head_to_head']['ttm20_wins']}/10 và hòa "
                f"{result['head_to_head']['ties']}/10.\n"
                f"- **Cả hai đều vượt VNINDEX trong mẫu này.** VNINDEX đạt "
                f"**{vn_metrics['cagr']:.2%}**; mỗi chiến lược thắng chỉ số "
                f"ở **{beats_ttm}/10** chu kỳ.\n"
                "- **Chưa nên coi đây là kết quả point-in-time tuyệt đối.** "
                "Dữ liệu trước 2018 chưa có ngày công bố xác thực và có thể "
                "đã bị cập nhật về sau."
            ),
        },
        {
            "id": "headline_metrics",
            "type": "metric-strip",
            "cardIds": ["ttm_card", "last8_card", "vn_card"],
        },
        {
            "id": "annual_finding",
            "type": "markdown",
            "sourceId": source["id"],
            "body": (
                "## Lợi thế thay đổi theo từng chu kỳ\n\n"
                "**Không có chiến lược nào dẫn dắt xuyên suốt.** LAST 8Q+ tốt "
                "hơn rõ ở 2015–2016 và 2023–2025, trong khi TTM ≤20Q vượt trội "
                "ở 2017–2018 và đặc biệt 2020–2021. Năm 2020–2021 là ngoại lệ "
                "lớn, vì vậy CAGR 10 năm bị kéo lên đáng kể."
            ),
        },
        {"id": "annual_chart", "type": "chart", "chartId": "annual_returns"},
        {
            "id": "annual_detail",
            "type": "markdown",
            "sourceId": source["id"],
            "body": (
                "## Đủ 10 chu kỳ và quy tắc số quý\n\n"
                "Ba chu kỳ đầu dùng lần lượt 8, 12 và 16 quý; bảy chu kỳ sau "
                "dùng đủ 20 quý. Mỗi danh mục đều có ít nhất 15 mã. Bảng dưới "
                "giữ nguyên lợi suất ròng, số mã và mức giao nhau để kiểm tra."
            ),
        },
        {"id": "annual_table", "type": "table", "tableId": "annual_table"},
        {
            "id": "sensitivity",
            "type": "markdown",
            "sourceId": source["id"],
            "body": (
                "## Kết luận vẫn giữ khi bỏ năm tăng đột biến\n\n"
                f"Nếu bỏ riêng chu kỳ 2020–2021, CAGR còn "
                f"**{_cagr(ex_peak_ttm):.2%}** cho TTM ≤20Q và "
                f"**{_cagr(ex_peak_last8):.2%}** cho LAST 8Q+. Chỉ xét bảy "
                f"chu kỳ có đủ 20 quý, CAGR tương ứng là "
                f"**{_cagr(full20_ttm):.2%}** và **{_cagr(full20_last8):.2%}**. "
                "LAST 8Q+ vẫn nhỉnh hơn nhẹ, nhưng khoảng cách nhỏ so với độ "
                "biến động giữa các năm."
            ),
        },
        {
            "id": "next_steps",
            "type": "markdown",
            "body": (
                "## Hướng dùng kết quả\n\n"
                "- Giữ LAST 8Q+ nếu ưu tiên danh mục cô đặc hơn và biến động "
                "thấp hơn; không nên mô tả nó là chiến lược có lợi nhuận vượt "
                "trội đã được chứng minh.\n"
                "- Giữ TTM ≤20Q nếu ưu tiên tập cơ hội rộng hơn và chấp nhận "
                "biến động cao hơn.\n"
                "- Trước khi chốt chiến lược sản xuất, cần backfill ngày công "
                "bố BCTC trước 2018 rồi chạy lại cùng snapshot bất biến."
            ),
        },
        {
            "id": "further_questions",
            "type": "markdown",
            "body": (
                "## Câu hỏi tiếp theo\n\n"
                "Mức giảm biến động của LAST 8Q+ có còn tồn tại khi đo max "
                "drawdown theo ngày và khi thay đổi vốn quỹ? Đây là kiểm tra "
                "tiếp theo có giá trị hơn việc tối ưu thêm trên 10 quan sát năm."
            ),
        },
        {
            "id": "caveats",
            "type": "markdown",
            "sourceId": source["id"],
            "body": (
                "## Giả định và giới hạn\n\n"
                "- Dữ liệu tài chính trước 2018 dùng kho legacy mutable với "
                "quy tắc độ trễ báo cáo, nên có rủi ro look-ahead/revision.\n"
                "- Vốn mô phỏng là 5 tỷ đồng; lô 100, ADV 20 phiên trước ngày "
                "mua, tỷ lệ tham gia và chi phí lấy từ cấu hình hiện tại.\n"
                "- Hiệu suất cổ phiếu dùng giá điều chỉnh Vietcap; VNINDEX "
                "không trừ phí. Không mô phỏng nạp/rút, trượt giá hay thuế cổ "
                "tức riêng ngoài tác động đã nằm trong chuỗi giá điều chỉnh.\n"
                "- Kết quả là snapshot nghiên cứu sinh ngày 29/07/2026, không "
                "phải cam kết hiệu suất tương lai."
            ),
        },
    ]
    manifest = {
        "version": 1,
        "surface": "report",
        "title": title,
        "description": "Backtest 10 chu kỳ, Top 10%, tối thiểu 15 mã.",
        "generatedAt": result["generated_at"],
        "sources": [source],
        "blocks": blocks,
        "cards": [
            {
                "id": "ttm_card",
                "description": "TTM dùng tối đa 20 quý",
                "dataset": "summary",
                "sourceId": source["id"],
                "metrics": [
                    {"label": "CAGR TTM ≤20Q", "field": "ttm_cagr", "format": "percent"},
                    {"label": "Hơn VNINDEX", "field": "ttm_excess_cagr", "format": "percent", "signed": True},
                ],
            },
            {
                "id": "last8_card",
                "description": "Thêm điều kiện 8 quý gần nhất dương",
                "dataset": "summary",
                "sourceId": source["id"],
                "metrics": [
                    {"label": "CAGR LAST 8Q+", "field": "last8_cagr", "format": "percent"},
                    {"label": "Hơn VNINDEX", "field": "last8_excess_cagr", "format": "percent", "signed": True},
                ],
            },
            {
                "id": "vn_card",
                "description": "Chỉ số tham chiếu cùng ngày",
                "dataset": "summary",
                "sourceId": source["id"],
                "metrics": [
                    {"label": "CAGR VNINDEX", "field": "vn_cagr", "format": "percent"},
                ],
            },
        ],
        "charts": [{
            "id": "annual_returns",
            "title": "Hiệu suất 12 tháng theo chu kỳ",
            "subtitle": "Cả hai chiến lược vượt VNINDEX ở 7/10 chu kỳ; 2020–2021 là năm vượt trội bất thường.",
            "intent": "comparison",
            "type": "bar",
            "dataset": "annual_long",
            "sourceId": source["id"],
            "encodings": {
                "x": {"field": "period", "type": "ordinal", "label": "Chu kỳ"},
                "y": {"field": "return_rate", "type": "quantitative", "format": "percent", "label": "Hiệu suất"},
                "color": {"field": "series", "type": "nominal", "label": "Danh mục"},
                "tooltip": [
                    {"field": "quarters", "type": "quantitative", "label": "Số quý EPS"},
                    {"field": "holdings", "type": "quantitative", "label": "Số mã"},
                ],
            },
            "valueFormat": "percent",
            "unit": "%",
            "layout": "full",
            "legend": {"position": "bottom"},
            "palette": {"kind": "categorical"},
            "settings": {"groupMode": "grouped", "orientation": "vertical", "sort": "custom"},
            "surface": {"surface": "card", "interactiveLegend": True, "showControls": True, "viewMode": "visualization"},
        }],
        "tables": [{
            "id": "annual_table",
            "title": "Kết quả từng chu kỳ",
            "subtitle": "Lợi suất ròng 12 tháng; số quý 8/12/16/20 theo lịch sử sẵn có.",
            "dataset": "annual_wide",
            "sourceId": source["id"],
            "layout": "full",
            "density": "dense",
            "defaultSort": {"field": "period", "direction": "asc"},
            "columns": [
                {"field": "period", "label": "Chu kỳ", "type": "text"},
                {"field": "quarters", "label": "Số quý", "format": "number"},
                {"field": "ttm_return", "label": "TTM ≤20Q", "format": "percent", "movement": True},
                {"field": "last8_return", "label": "LAST 8Q+", "format": "percent", "movement": True},
                {"field": "vn_return", "label": "VNINDEX", "format": "percent", "movement": True},
                {"field": "last8_minus_ttm", "label": "8Q+ - TTM", "format": "percent", "movement": True},
                {"field": "ttm_holdings", "label": "Mã TTM", "format": "number"},
                {"field": "last8_holdings", "label": "Mã 8Q+", "format": "number"},
                {"field": "overlap", "label": "Mã trùng", "format": "number"},
            ],
        }],
    }
    return {
        "surface": "report",
        "manifest": manifest,
        "snapshot": {
            "version": 1,
            "generatedAt": result["generated_at"],
            "status": "ready",
            "datasets": {
                "summary": summary,
                "annual_wide": annual_wide,
                "annual_long": annual_long,
            },
        },
        "sources": [source],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = json.loads(args.input.read_text(encoding="utf-8"))
    artifact = build(result, args.input.as_posix())
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
