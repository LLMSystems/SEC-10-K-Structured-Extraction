from __future__ import annotations

import re
from html import escape
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, NavigableString
from tabulate import tabulate
from tabulate_html import TableParser


KEEP_ATTRS = {"colspan", "rowspan", "href"}
KEEP_TAGS = {
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "th",
    "td",
    "p",
    "div",
    "br",
    "ul",
    "ol",
    "li",
    "a",
}

PRIMARY_STATEMENT_ORDER = [
    "income_statement",
    "comprehensive_income",
    "balance_sheet",
    "shareholders_equity",
    "cash_flow_statement",
]

PRIMARY_STATEMENT_TITLES = {
    "income_statement": "Consolidated Statements of Operations",
    "comprehensive_income": "Consolidated Statements of Comprehensive Income",
    "balance_sheet": "Consolidated Balance Sheets",
    "shareholders_equity": "Consolidated Statements of Shareholders' Equity",
    "cash_flow_statement": "Consolidated Statements of Cash Flows",
}

__all__ = ["render_markdown", "write_item8_markdown"]


def humanize_statement_key(statement_key: str) -> str:
    return PRIMARY_STATEMENT_TITLES.get(
        statement_key,
        statement_key.replace("_", " ").title(),
    )


def normalize_period_label(period_key: str) -> str:
    if period_key.startswith("duration:"):
        _, start_date, end_date = period_key.split(":", 2)
        year = end_date[:4]
        return f"FY{year} ({start_date} to {end_date})"
    if period_key.startswith("instant:"):
        _, instant = period_key.split(":", 1)
        return instant
    return period_key


def normalize_period_labels(periods: list[str]) -> dict[str, str]:
    return {period: normalize_period_label(period) for period in periods}


def format_numeric_value(raw_value: str, unit: str | None) -> str:
    if raw_value is None:
        return ""

    raw_value = str(raw_value).strip()
    if raw_value == "":
        return ""

    try:
        if re.fullmatch(r"-?\d+", raw_value):
            integer_value = int(raw_value)
            if unit == "iso4217:USD":
                return f"{integer_value:,}"
            return f"{integer_value:,}"

        if re.fullmatch(r"-?\d+\.\d+", raw_value):
            decimal_value = float(raw_value)
            if unit == "iso4217:USD/shares":
                return f"{decimal_value:.2f}"
            return f"{decimal_value:,.2f}".rstrip("0").rstrip(".")
    except ValueError:
        return raw_value

    return raw_value


def infer_unit_suffix(unit: str | None) -> str:
    if unit == "iso4217:USD":
        return "USD"
    if unit == "iso4217:USD/shares":
        return "USD/share"
    if unit == "shares":
        return "shares"
    return unit or ""


def render_markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_No rows available._"

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        escaped = [cell.replace("\n", "<br/>") for cell in row]
        lines.append("| " + " | ".join(escaped) + " |")
    return "\n".join(lines)


def render_readable_statements(payload: dict[str, Any]) -> list[str]:
    readable_statements = payload.get("readable_statements", {})
    lines: list[str] = ["# Main Statements", ""]

    for statement_key in PRIMARY_STATEMENT_ORDER:
        statement = readable_statements.get(statement_key)
        if not statement:
            continue

        periods = statement.get("periods", [])
        if not periods:
            continue

        period_labels = normalize_period_labels(periods)
        lines.append(f"## {humanize_statement_key(statement_key)}")
        lines.append("")

        headers = ["Line Item"] + [period_labels[period] for period in periods]
        rows: list[list[str]] = []
        for line_item in statement.get("line_items", []):
            row = [line_item.get("label", line_item.get("concept", ""))]
            values = line_item.get("values", {})
            first_unit = ""
            for period in periods:
                value_info = values.get(period)
                if value_info is None:
                    row.append("")
                    continue
                formatted = format_numeric_value(value_info.get("value", ""), value_info.get("unit"))
                row.append(formatted)
                first_unit = first_unit or infer_unit_suffix(value_info.get("unit"))
            if first_unit:
                row[0] = f"{row[0]} ({first_unit})"
            rows.append(row)

        lines.append(render_markdown_table(headers, rows))
        lines.append("")

    return lines


def sanitize_html_fragment(fragment: str) -> str:
    fragment = re.sub(r"<(td|th|tr|table)(colspan|rowspan|href)=", r"<\1 \2=", fragment, flags=re.IGNORECASE)
    fragment = re.sub(r"(</?\w+)([A-Za-z_:][-A-Za-z0-9_:.]*=)", r"\1 \2", fragment)

    soup = BeautifulSoup(fragment, "html.parser")

    for tag in soup.find_all(["style", "script"]):
        tag.decompose()

    for tag in soup.find_all(True):
        if tag.name not in KEEP_TAGS:
            tag.unwrap()
            continue
        tag.attrs = {
            key: value
            for key, value in tag.attrs.items()
            if key in KEEP_ATTRS
        }

    # Inside tables, layout-only wrappers should not survive.
    for tag in soup.find_all(["div", "p", "span"]):
        if tag.find_parent(["table", "thead", "tbody", "tfoot", "tr", "td", "th"]):
            tag.unwrap()

    return str(soup).strip()


def build_table_grid_with_tabulate_html(table_html: str) -> list[list[str]] | None:
    try:
        parser = TableParser()
        grid = parser._build_grid([table_html])
        if isinstance(grid, list):
            return grid
    except Exception:
        return None
    return None


def is_blank_cell(cell) -> bool:
    return cell is None or str(cell).strip() == ""

def drop_blank_rows(grid: list[list[str]]) -> list[list[str]]:
    return [
        row for row in grid
        if not all(is_blank_cell(cell) for cell in row)
    ]

def drop_blank_cols(grid: list[list[str]]) -> list[list[str]]:
    if not grid:
        return grid

    width = max(len(row) for row in grid)
    normalized = [
        row + [""] * (width - len(row))
        for row in grid
    ]

    keep_col_indexes = [
        col_idx
        for col_idx in range(width)
        if not all(is_blank_cell(row[col_idx]) for row in normalized)
    ]

    return [
        [row[col_idx] for col_idx in keep_col_indexes]
        for row in normalized
    ]

def table_html_to_markdown(table_html: str) -> str:
    grid = build_table_grid_with_tabulate_html(table_html)
    if not grid:
        return sanitize_html_fragment(table_html)

    grid = [
        [normalize_grid_cell(cell) for cell in row]
        for row in grid
    ]
    grid = drop_blank_rows(grid)
    grid = drop_blank_cols(grid)
    grid = [merge_currency_cells_in_grid_row(row) for row in grid]
    grid = drop_blank_cols(grid)
    grid = drop_same_header_columns_with_empty_body(grid)
    grid = drop_duplicate_adjacent_columns(grid)

    markdown_table = tabulate(grid, headers="firstrow", tablefmt="github")
    return markdown_table


def drop_duplicate_adjacent_columns(grid: list[list[str]]) -> list[list[str]]:
    if not grid:
        return grid

    width = max(len(row) for row in grid)
    normalized = [
        row + [""] * (width - len(row))
        for row in grid
    ]

    keep_col_indexes = [0] if width > 0 else []
    for col_idx in range(1, width):
        prev_idx = keep_col_indexes[-1]
        if columns_are_equivalent(normalized, prev_idx, col_idx):
            continue
        keep_col_indexes.append(col_idx)

    collapsed_grid = [
        [row[col_idx] for col_idx in keep_col_indexes]
        for row in normalized
    ]
    return drop_blank_cols(collapsed_grid)


def drop_same_header_columns_with_empty_body(grid: list[list[str]]) -> list[list[str]]:
    if not grid:
        return grid

    width = max(len(row) for row in grid)
    normalized = [
        row + [""] * (width - len(row))
        for row in grid
    ]

    header = normalized[0]
    keep_col_indexes: list[int] = []

    start = 0
    while start < width:
        end = start + 1
        while end < width and header[end] == header[start]:
            end += 1

        group_indexes = list(range(start, end))
        if is_blank_cell(header[start]):
            keep_col_indexes.extend(group_indexes)
        else:
            kept_in_group = [
                idx for idx in group_indexes
                if column_has_body_value(normalized, idx)
            ]
            if kept_in_group:
                keep_col_indexes.extend(kept_in_group)
            else:
                keep_col_indexes.append(group_indexes[0])

        start = end

    reduced = [
        [row[col_idx] for col_idx in keep_col_indexes]
        for row in normalized
    ]
    return drop_blank_cols(reduced)


def column_has_body_value(grid: list[list[str]], col_idx: int) -> bool:
    for row in grid[1:]:
        if not is_blank_cell(row[col_idx]):
            return True
    return False


def columns_are_equivalent(grid: list[list[str]], left_idx: int, right_idx: int) -> bool:
    for row in grid:
        left = normalize_grid_cell(row[left_idx])
        right = normalize_grid_cell(row[right_idx])
        if left != right:
            return False
    return True


def normalize_grid_cell(cell: Any) -> str:
    text = str(cell or "")
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def is_numeric_like(text: str) -> bool:
    cleaned = str(text).replace(",", "").replace("$", "").replace("%", "").strip()
    return bool(re.fullmatch(r"-?\d+(\.\d+)?", cleaned))


def merge_currency_cells_in_grid_row(row: list[str]) -> list[str]:
    merged = list(row)
    for idx in range(len(merged) - 1):
        current = merged[idx]
        nxt = merged[idx + 1]
        if current == "$" and nxt:
            merged[idx] = f"${nxt}"
            merged[idx + 1] = ""
            continue
        if current and nxt == "%" and is_numeric_like(current):
            merged[idx] = f"{current}%"
            merged[idx + 1] = ""
    return merged


def html_fragment_to_markdownish_text(fragment: str) -> str:
    soup = BeautifulSoup(fragment, "html.parser")

    for br in soup.find_all("br"):
        br.replace_with("\n")

    block_names = {"div", "p", "li"}
    for tag in soup.find_all(block_names):
        text = tag.get_text(" ", strip=True)
        replacement = f"{text}\n\n" if text else "\n"
        tag.replace_with(NavigableString(replacement))

    for list_tag in soup.find_all(["ul", "ol"]):
        list_tag.unwrap()

    for tag in soup.find_all(True):
        if tag.name == "a":
            href = tag.get("href")
            text = tag.get_text(" ", strip=True)
            if href and text:
                tag.replace_with(NavigableString(f"[{text}]({href})"))
            else:
                tag.replace_with(NavigableString(text))
        else:
            tag.unwrap()

    text = soup.get_text()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def render_html_fragment_with_tables(fragment: str) -> str:
    soup = BeautifulSoup(fragment, "html.parser")
    chunks: list[str] = []

    for table in soup.find_all("table"):
        placeholder = soup.new_string(f"__TABLE_PLACEHOLDER_{len(chunks)}__")
        table.replace_with(placeholder)
        chunks.append(str(table))

    text_with_placeholders = html_fragment_to_markdownish_text(str(soup))
    parts = re.split(r"(__TABLE_PLACEHOLDER_\d+__)", text_with_placeholders)

    rendered: list[str] = []
    for part in parts:
        match = re.fullmatch(r"__TABLE_PLACEHOLDER_(\d+)__", part.strip())
        if match:
            rendered.append(table_html_to_markdown(chunks[int(match.group(1))]))
        else:
            cleaned = part.strip()
            if cleaned:
                rendered.append(cleaned)

    return "\n\n".join(rendered).strip()


def render_numeric_disclosure_role(role: dict[str, Any]) -> list[str]:
    lines: list[str] = [
        f"## {role.get('role_definition', 'Numeric Disclosure')}",
        "",
    ]

    periods = role.get("periods", [])
    period_labels = normalize_period_labels(periods)

    scalar_rows: list[list[str]] = []
    complex_items: list[dict[str, Any]] = []

    for line_item in role.get("line_items", []):
        values = line_item.get("values", {})
        has_multi_value_period = any(isinstance(values.get(period), list) for period in periods)
        if has_multi_value_period:
            complex_items.append(line_item)
            continue

        row = [line_item.get("label", line_item.get("concept", ""))]
        first_unit = ""
        for period in periods:
            value_info = values.get(period)
            if not value_info:
                row.append("")
                continue
            formatted = format_numeric_value(value_info.get("value", ""), value_info.get("unit"))
            row.append(formatted)
            first_unit = first_unit or infer_unit_suffix(value_info.get("unit"))
        if first_unit:
            row[0] = f"{row[0]} ({first_unit})"
        scalar_rows.append(row)

    if scalar_rows:
        headers = ["Line Item"] + [period_labels[period] for period in periods]
        lines.append(render_markdown_table(headers, scalar_rows))
        lines.append("")

    for line_item in complex_items:
        lines.append(f"### {line_item.get('label', line_item.get('concept', 'Disclosure Item'))}")
        lines.append("")

        for period in periods:
            period_value = line_item.get("values", {}).get(period)
            if not period_value:
                continue

            lines.append(f"#### {period_labels[period]}")
            lines.append("")

            if isinstance(period_value, list):
                rows: list[list[str]] = []
                for entry in period_value:
                    dimension_text = format_dimensions(entry.get("dimensions", []))
                    rows.append(
                        [
                            dimension_text,
                            format_numeric_value(entry.get("value", ""), entry.get("unit")),
                            infer_unit_suffix(entry.get("unit")),
                        ]
                    )
                lines.append(
                    render_markdown_table(
                        ["Dimensions", "Value", "Unit"],
                        rows,
                    )
                )
                lines.append("")
            else:
                lines.append(
                    render_markdown_table(
                        ["Value", "Unit"],
                        [[
                            format_numeric_value(period_value.get("value", ""), period_value.get("unit")),
                            infer_unit_suffix(period_value.get("unit")),
                        ]],
                    )
                )
                lines.append("")

    return lines


def format_dimensions(dimensions: list[dict[str, str]]) -> str:
    if not dimensions:
        return "Consolidated"
    parts = []
    for dimension in dimensions:
        dim_name = dimension.get("dimension", "")
        member_name = dimension.get("member", "")
        parts.append(f"{dim_name} = {member_name}")
    return "<br/>".join(parts)


def render_numeric_disclosures(payload: dict[str, Any]) -> list[str]:
    roles = payload.get("readable_numeric_disclosures", [])
    lines: list[str] = ["# Numeric Disclosures", ""]

    if not roles:
        lines.append("_No numeric disclosures available._")
        lines.append("")
        return lines

    for role in roles:
        lines.extend(render_numeric_disclosure_role(role))

    return lines


def render_text_disclosure_fact(fact: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    label = fact.get("label", fact.get("concept", "Text Disclosure"))
    value = fact.get("value", "")

    lines.append(f"### {label}")
    lines.append("")

    sanitized = sanitize_html_fragment(value)
    if "<table" in sanitized.lower():
        mixed_output = render_html_fragment_with_tables(sanitized)
        lines.append(mixed_output)
        lines.append("")
        return lines

    text_output = html_fragment_to_markdownish_text(sanitized)
    if text_output:
        lines.append(text_output)
    else:
        lines.append(escape(value))
    lines.append("")
    return lines


def render_text_disclosures(payload: dict[str, Any]) -> list[str]:
    roles = payload.get("text_disclosures", [])
    lines: list[str] = ["# Text Disclosures", ""]

    if not roles:
        lines.append("_No text disclosures available._")
        lines.append("")
        return lines

    for role in roles:
        lines.append(f"## {role.get('role_definition', 'Text Disclosure')}")
        lines.append("")
        for fact in role.get("text_facts", []):
            lines.extend(render_text_disclosure_fact(fact))

    return lines


def render_header(payload: dict[str, Any]) -> list[str]:
    filing_info = payload.get("filing_info", {})
    summary = payload.get("summary", {})
    filing_dir_url = filing_info.get("filing_dir_url", "")

    lines = [
        f"# {filing_info.get('company_name', 'Unknown Company')} Item 8 Report",
        "",
        f"- CIK: `{filing_info.get('cik', '')}`",
        f"- Accession Number: `{filing_info.get('accession_number', '')}`",
        f"- Mode: `{summary.get('mode', '')}`",
        f"- Main Statements: `{summary.get('statement_count', 0)}`",
        f"- Numeric Disclosures: `{summary.get('numeric_disclosure_count', 0)}`",
        f"- Text Disclosures: `{summary.get('text_disclosure_count', 0)}`",
    ]

    if filing_dir_url:
        lines.append(f"- Filing Directory: {filing_dir_url}")

    lines.extend(
        [
            "",
            "## Contents",
            "",
            "- Main Statements",
            "- Numeric Disclosures",
            "- Text Disclosures",
            "",
        ]
    )
    return lines


def render_markdown(payload: dict[str, Any]) -> str:
    sections: list[str] = []
    for chunk in (
        render_header(payload),
        render_readable_statements(payload),
        render_numeric_disclosures(payload),
        render_text_disclosures(payload),
    ):
        sections.append("\n".join(chunk).rstrip())

    return "\n\n".join(section for section in sections if section).strip() + "\n"


def write_item8_markdown(payload: dict[str, Any], output_path: str | Path) -> Path:
    output_path = Path(output_path)
    markdown = render_markdown(payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")
    return output_path
