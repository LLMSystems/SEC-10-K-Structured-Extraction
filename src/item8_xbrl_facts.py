from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import urljoin
from zipfile import ZipFile
from typing import Any

import requests
from lxml import etree


USER_AGENT = "10K-Parser contact@example.com"
BASE_URL = "https://www.sec.gov"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

XLINK_NS = "http://www.w3.org/1999/xlink"
LINK_NS = "http://www.xbrl.org/2003/linkbase"
XBRLI_NS = "http://www.xbrl.org/2003/instance"
XBRLDI_NS = "http://xbrl.org/2006/xbrldi"

NS = {
    "link": LINK_NS,
    "xlink": XLINK_NS,
    "xbrli": XBRLI_NS,
    "xbrldi": XBRLDI_NS,
}

ROLE_KEYWORDS = (
    "statement -",
    "disclosure -",
    "schedule -",
)
STATEMENT_ROLE_KEYWORD = "statement -"
ROLE_EXCLUDES = (
    "document -",
    "parenthetical",
    "cover",
)

__all__ = ["get_item8_xbrl_facts"]


@dataclass
class FilingPaths:
    cik: str
    accession_number: str
    company_name: str
    accession_nodash: str
    filing_dir_url: str
    instance_url: str
    presentation_url: str
    label_url: str
    schema_url: str


class SecClient:
    def __init__(self, user_agent: str = USER_AGENT):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept-Encoding": "gzip, deflate",
            }
        )

    def get_json(self, url: str) -> dict[str, Any]:
        response = self.session.get(url, timeout=60)
        response.raise_for_status()
        return response.json()

    def get_bytes(self, url: str) -> bytes:
        response = self.session.get(url, timeout=60)
        response.raise_for_status()
        return response.content


def normalize_cik(cik: str) -> str:
    digits = "".join(ch for ch in cik if ch.isdigit())
    if not digits:
        raise ValueError("CIK must contain digits.")
    return digits.zfill(10)


def normalize_accession(accession_number: str) -> str:
    value = accession_number.strip()
    if not value:
        raise ValueError("accession_number is required.")
    return value


def parse_xml(content: bytes) -> etree._Element:
    return etree.fromstring(content, parser=etree.XMLParser(recover=True, huge_tree=True))


def build_zip_resource_url(zip_url: str, entry_name: str) -> str:
    return f"{zip_url}#zip-entry={entry_name}"


def read_resource_bytes(client: SecClient, resource_url: str) -> bytes:
    marker = "#zip-entry="
    if marker not in resource_url:
        return client.get_bytes(resource_url)

    zip_url, entry_name = resource_url.split(marker, 1)
    zip_bytes = client.get_bytes(zip_url)
    with ZipFile(BytesIO(zip_bytes)) as archive:
        return archive.read(entry_name)


def qname_to_prefixed_name(element: etree._Element) -> str:
    qname = etree.QName(element.tag)
    namespace = qname.namespace
    localname = qname.localname

    root = element.getroottree().getroot()
    for prefix, uri in (root.nsmap or {}).items():
        if uri == namespace and prefix:
            return f"{prefix}:{localname}"
    return localname


def fragment_to_concept_name(href: str) -> str | None:
    if "#" not in href:
        return None
    fragment = href.split("#", 1)[1]
    if "_" not in fragment:
        return fragment
    prefix, local = fragment.split("_", 1)
    return f"{prefix}:{local}"


def build_filing_paths(client: SecClient, cik: str, accession_number: str) -> FilingPaths:
    cik_padded = normalize_cik(cik)
    accession = normalize_accession(accession_number)
    accession_nodash = accession.replace("-", "")
    cik_unpadded = str(int(cik_padded))

    submissions = client.get_json(SUBMISSIONS_URL.format(cik=cik_padded))
    company_name = submissions.get("name", "Unknown")

    filing_dir_url = f"{BASE_URL}/Archives/edgar/data/{cik_unpadded}/{accession_nodash}"
    index_url = f"{filing_dir_url}/index.json"
    index_payload = client.get_json(index_url)

    items = index_payload.get("directory", {}).get("item", [])
    names = [item.get("name", "") for item in items]
    xbrl_zip_name = next((name for name in names if name.lower().endswith("-xbrl.zip")), None)
    xbrl_zip_url = f"{filing_dir_url}/{xbrl_zip_name}" if xbrl_zip_name else None

    schema_name = pick_first(
        names,
        lambda name: name.endswith(".xsd"),
        "schema (.xsd)",
    )
    schema_url = f"{filing_dir_url}/{schema_name}"
    schema_root = parse_xml(client.get_bytes(schema_url))
    presentation_resource = find_linkbase_name(
        schema_root=schema_root,
        filing_dir_url=filing_dir_url,
        schema_url=schema_url,
        names=names,
        xbrl_zip_url=xbrl_zip_url,
        role_hint="presentationLinkbaseRef",
        fallback_predicate=lambda name: name.endswith("_pre.xml") or name.endswith(".pre.xml"),
        label="presentation linkbase (_pre.xml)",
    )
    label_resource = find_linkbase_name(
        schema_root=schema_root,
        filing_dir_url=filing_dir_url,
        schema_url=schema_url,
        names=names,
        xbrl_zip_url=xbrl_zip_url,
        role_hint="labelLinkbaseRef",
        fallback_predicate=lambda name: name.endswith("_lab.xml") or name.endswith(".lab.xml"),
        label="label linkbase (_lab.xml)",
    )
    instance_name = find_instance_document_name(client, filing_dir_url, names)

    return FilingPaths(
        cik=cik_padded,
        accession_number=accession,
        company_name=company_name,
        accession_nodash=accession_nodash,
        filing_dir_url=filing_dir_url,
        instance_url=f"{filing_dir_url}/{instance_name}",
        presentation_url=presentation_resource,
        label_url=label_resource,
        schema_url=schema_url,
    )


def pick_first(names: list[str], predicate, label: str) -> str:
    for name in names:
        if predicate(name):
            return name
    raise ValueError(f"Could not locate {label} in filing directory.")


def is_instance_document_name(name: str) -> bool:
    if not name.endswith(".xml"):
        return False

    lowered = name.lower()
    if lowered == "filingsummary.xml":
        return False

    excluded_suffixes = (
        ".xsd",
        "_cal.xml",
        "_def.xml",
        "_lab.xml",
        "_pre.xml",
    )
    if lowered.endswith(excluded_suffixes):
        return False

    return True


def is_xbrl_instance_root(root: etree._Element) -> bool:
    return root.tag == f"{{{XBRLI_NS}}}xbrl"


def basename_from_href(href: str) -> str:
    cleaned = href.split("#", 1)[0].split("?", 1)[0]
    return cleaned.rsplit("/", 1)[-1]


def find_linkbase_name(
    schema_root: etree._Element,
    filing_dir_url: str,
    schema_url: str,
    names: list[str],
    xbrl_zip_url: str | None,
    role_hint: str,
    fallback_predicate,
    label: str,
) -> str:
    schema_base_url = f"{filing_dir_url}/"

    for linkbase_ref in schema_root.xpath(".//*[@xlink:href]", namespaces=NS):
        href = linkbase_ref.get(f"{{{XLINK_NS}}}href", "")
        role = linkbase_ref.get(f"{{{XLINK_NS}}}role", "")
        if role_hint not in role and role_hint.lower() not in href.lower():
            continue

        candidate_name = basename_from_href(urljoin(schema_base_url, href))
        if candidate_name in names:
            return f"{filing_dir_url}/{candidate_name}"
        if xbrl_zip_url:
            return build_zip_resource_url(xbrl_zip_url, candidate_name)

    if role_hint == "presentationLinkbaseRef" and schema_root.xpath(".//link:presentationLink", namespaces=NS):
        return schema_url
    if role_hint == "labelLinkbaseRef" and schema_root.xpath(".//link:labelLink", namespaces=NS):
        return schema_url

    fallback_name = pick_first(names, fallback_predicate, label)
    return f"{filing_dir_url}/{fallback_name}"


def find_instance_document_name(client: SecClient, filing_dir_url: str, names: list[str]) -> str:
    candidate_names = [name for name in names if is_instance_document_name(name)]
    if not candidate_names:
        raise ValueError("Could not locate any candidate XML files for XBRL instance document.")

    for name in candidate_names:
        candidate_url = f"{filing_dir_url}/{name}"
        try:
            root = parse_xml(client.get_bytes(candidate_url))
        except Exception:
            continue
        if is_xbrl_instance_root(root):
            return name

    raise ValueError(
        "Could not locate a valid XBRL instance document. Candidate XML files did not have xbrli:xbrl root."
    )


def parse_role_definitions(schema_root: etree._Element) -> dict[str, str]:
    role_map: dict[str, str] = {}
    for role_type in schema_root.xpath(".//link:roleType", namespaces=NS):
        role_uri = role_type.get("roleURI")
        definition = role_type.findtext(f"{{{LINK_NS}}}definition")
        if role_uri and definition:
            role_map[role_uri] = " ".join(definition.split())
    return role_map


def parse_labels(label_root: etree._Element) -> dict[str, str]:
    labels_by_role: dict[str, dict[str, str]] = defaultdict(dict)

    for label_link in label_root.xpath(".//link:labelLink", namespaces=NS):
        locators = {
            loc.get(f"{{{XLINK_NS}}}label"): fragment_to_concept_name(loc.get(f"{{{XLINK_NS}}}href", ""))
            for loc in label_link.xpath("./link:loc", namespaces=NS)
        }
        resources = {
            resource.get(f"{{{XLINK_NS}}}label"): {
                "text": " ".join("".join(resource.itertext()).split()),
                "role": resource.get(f"{{{XLINK_NS}}}role", ""),
            }
            for resource in label_link.xpath("./link:label", namespaces=NS)
        }

        for arc in label_link.xpath("./link:labelArc", namespaces=NS):
            from_label = arc.get(f"{{{XLINK_NS}}}from")
            to_label = arc.get(f"{{{XLINK_NS}}}to")
            concept_name = locators.get(from_label)
            resource = resources.get(to_label)
            if not concept_name or not resource:
                continue
            labels_by_role[concept_name][resource["role"]] = resource["text"]

    final_labels: dict[str, str] = {}
    for concept_name, role_map in labels_by_role.items():
        final_labels[concept_name] = (
            role_map.get("http://www.xbrl.org/2003/role/label")
            or next(iter(role_map.values()), concept_name)
        )
    return final_labels


def parse_presentation_roles(
    presentation_root: etree._Element,
    role_definitions: dict[str, str],
    labels: dict[str, str],
) -> list[dict[str, Any]]:
    roles: list[dict[str, Any]] = []

    for link in presentation_root.xpath(".//link:presentationLink", namespaces=NS):
        role_uri = link.get(f"{{{XLINK_NS}}}role", "")
        role_definition = role_definitions.get(role_uri, role_uri)
        definition_lower = role_definition.lower()
        if not any(keyword in definition_lower for keyword in ROLE_KEYWORDS):
            continue
        if any(keyword in definition_lower for keyword in ROLE_EXCLUDES):
            continue

        locators = {
            loc.get(f"{{{XLINK_NS}}}label"): fragment_to_concept_name(loc.get(f"{{{XLINK_NS}}}href", ""))
            for loc in link.xpath("./link:loc", namespaces=NS)
        }

        concepts = [name for name in locators.values() if name]
        roles.append(
            {
                "role_uri": role_uri,
                "role_definition": role_definition,
                "role_kind": classify_role_definition(role_definition),
                "concepts": concepts,
                "display_labels": {name: labels.get(name, name) for name in concepts},
            }
        )

    return roles


def classify_role_definition(role_definition: str) -> str:
    definition_lower = role_definition.lower()
    if STATEMENT_ROLE_KEYWORD in definition_lower:
        return "statement"
    if "disclosure -" in definition_lower:
        return "disclosure"
    if "schedule -" in definition_lower:
        return "schedule"
    return "other"


def parse_contexts(instance_root: etree._Element) -> dict[str, dict[str, Any]]:
    contexts: dict[str, dict[str, Any]] = {}

    for context in instance_root.xpath(".//xbrli:context", namespaces=NS):
        context_id = context.get("id")
        if not context_id:
            continue

        entity_identifier = context.findtext("./xbrli:entity/xbrli:identifier", namespaces=NS)

        instant = context.findtext("./xbrli:period/xbrli:instant", namespaces=NS)
        start_date = context.findtext("./xbrli:period/xbrli:startDate", namespaces=NS)
        end_date = context.findtext("./xbrli:period/xbrli:endDate", namespaces=NS)

        dimensions: list[dict[str, str]] = []
        for member in context.xpath(".//xbrldi:explicitMember", namespaces=NS):
            dimensions.append(
                {
                    "dimension": member.get("dimension", ""),
                    "member": (member.text or "").strip(),
                }
            )

        contexts[context_id] = {
            "entity_identifier": entity_identifier,
            "period": {
                "instant": instant,
                "start_date": start_date,
                "end_date": end_date,
            },
            "dimensions": dimensions,
        }

    return contexts


def parse_units(instance_root: etree._Element) -> dict[str, str]:
    units: dict[str, str] = {}

    for unit in instance_root.xpath(".//xbrli:unit", namespaces=NS):
        unit_id = unit.get("id")
        if not unit_id:
            continue

        measures = [
            (measure.text or "").strip()
            for measure in unit.xpath("./xbrli:measure", namespaces=NS)
            if (measure.text or "").strip()
        ]

        if measures:
            units[unit_id] = "*".join(measures)
            continue

        divide = unit.find("./xbrli:divide", namespaces=NS)
        if divide is not None:
            numerator = [
                (measure.text or "").strip()
                for measure in divide.xpath("./xbrli:unitNumerator/xbrli:measure", namespaces=NS)
                if (measure.text or "").strip()
            ]
            denominator = [
                (measure.text or "").strip()
                for measure in divide.xpath("./xbrli:unitDenominator/xbrli:measure", namespaces=NS)
                if (measure.text or "").strip()
            ]
            units[unit_id] = f"{'*'.join(numerator)}/{'*'.join(denominator)}"

    return units


def parse_facts(
    instance_root: etree._Element,
    contexts: dict[str, dict[str, Any]],
    units: dict[str, str],
    labels: dict[str, str],
) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []

    for element in instance_root.iter():
        if not isinstance(element.tag, str):
            continue
        if element.tag.startswith(f"{{{XBRLI_NS}}}"):
            continue

        context_ref = element.get("contextRef")
        if not context_ref:
            continue

        raw_value = (element.text or "").strip()
        if not raw_value:
            continue

        concept_name = qname_to_prefixed_name(element)
        facts.append(
            {
                "concept": concept_name,
                "label": labels.get(concept_name, concept_name),
                "value": raw_value,
                "decimals": element.get("decimals"),
                "scale": element.get("scale"),
                "sign": element.get("sign"),
                "unit": units.get(element.get("unitRef", "")),
                "context_id": context_ref,
                "context": contexts.get(context_ref),
            }
        )

    return facts


def filter_roles_by_mode(roles: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    if mode == "statements_only":
        return [role for role in roles if role["role_kind"] == "statement"]
    if mode == "statements_and_notes":
        return roles
    raise ValueError(f"Unsupported mode: {mode}")


def dedupe_role_facts(role_facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    for fact in role_facts:
        dedupe_key = (
            fact["concept"],
            fact["context_id"],
            fact["value"],
            fact["unit"],
            fact["decimals"],
            fact["scale"],
            fact["sign"],
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        deduped.append(fact)

    return deduped


def unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def statement_key_from_definition(role_definition: str) -> str | None:
    definition_upper = role_definition.upper()
    if "STATEMENTS OF OPERATIONS" in definition_upper or "STATEMENT OF OPERATIONS" in definition_upper:
        return "income_statement"
    if "STATEMENTS OF COMPREHENSIVE INCOME" in definition_upper or "STATEMENT OF COMPREHENSIVE INCOME" in definition_upper:
        return "comprehensive_income"
    if "BALANCE SHEETS" in definition_upper or "BALANCE SHEET" in definition_upper:
        return "balance_sheet"
    if "STATEMENTS OF SHAREHOLDERS' EQUITY" in definition_upper or "STATEMENT OF SHAREHOLDERS' EQUITY" in definition_upper:
        return "shareholders_equity"
    if "STATEMENTS OF CASH FLOWS" in definition_upper or "STATEMENT OF CASH FLOWS" in definition_upper:
        return "cash_flow_statement"
    return None


def build_statement_map(role_outputs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    statements: dict[str, dict[str, Any]] = {}
    for role in role_outputs:
        if role.get("role_kind") != "statement":
            continue
        statement_key = statement_key_from_definition(role["role_definition"])
        if not statement_key:
            continue
        statements[statement_key] = role
    return statements


def build_other_financial_roles(role_outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        role
        for role in role_outputs
        if role.get("role_kind") != "statement"
    ]


def is_probably_text_fact(fact: dict[str, Any]) -> bool:
    concept = fact.get("concept", "")
    unit = fact.get("unit")
    value = fact.get("value", "")

    if concept.endswith("TextBlock"):
        return True

    if unit is not None:
        return False

    if any(ch in value for ch in "<>/"):
        return True

    if len(value) >= 200:
        return True

    return False


def split_other_financial_roles(
    other_financial_roles: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    text_roles: list[dict[str, Any]] = []
    numeric_roles: list[dict[str, Any]] = []

    for role in other_financial_roles:
        facts = role.get("facts", [])
        text_facts = [fact for fact in facts if is_probably_text_fact(fact)]
        numeric_facts = [fact for fact in facts if not is_probably_text_fact(fact)]
        text_count = len(text_facts)
        numeric_count = len(numeric_facts)

        enriched_role = {
            **role,
            "text_fact_count": text_count,
            "numeric_fact_count": numeric_count,
            "text_facts": text_facts,
            "numeric_facts": numeric_facts,
        }

        if text_count > 0:
            text_roles.append(enriched_role)
        else:
            numeric_roles.append(enriched_role)

    return text_roles, numeric_roles


def is_consolidated_fact(fact: dict[str, Any]) -> bool:
    return not fact.get("context", {}).get("dimensions")


def period_sort_key(period_key: str) -> tuple[int, str]:
    if period_key.startswith("duration:"):
        return (0, period_key)
    if period_key.startswith("instant:"):
        return (1, period_key)
    return (2, period_key)


def build_period_key(period: dict[str, Any]) -> str:
    instant = period.get("instant")
    start_date = period.get("start_date")
    end_date = period.get("end_date")
    if instant:
        return f"instant:{instant}"
    if start_date and end_date:
        return f"duration:{start_date}:{end_date}"
    return "unknown"


def simplify_fact(fact: dict[str, Any]) -> dict[str, Any]:
    context = fact.get("context") or {}
    period = context.get("period") or {}
    return {
        "concept": fact["concept"],
        "label": fact["label"],
        "value": fact["value"],
        "unit": fact["unit"],
        "decimals": fact["decimals"],
        "period": period,
        "dimensions": context.get("dimensions") or [],
        "context_id": fact["context_id"],
    }


def build_consolidated_view(statements: dict[str, dict[str, Any]]) -> dict[str, Any]:
    consolidated: dict[str, Any] = {}

    for statement_key, statement in statements.items():
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for fact in statement.get("facts", []):
            if not is_consolidated_fact(fact):
                continue
            period_key = build_period_key((fact.get("context") or {}).get("period") or {})
            grouped[period_key].append(simplify_fact(fact))

        consolidated[statement_key] = {
            "role_definition": statement["role_definition"],
            "fact_count": sum(len(facts) for facts in grouped.values()),
            "periods": {
                period_key: grouped[period_key]
                for period_key in sorted(grouped.keys(), key=period_sort_key)
            },
        }

    return consolidated


def build_readable_statements(statements: dict[str, dict[str, Any]]) -> dict[str, Any]:
    readable: dict[str, Any] = {}

    for statement_key, statement in statements.items():
        concept_order = statement.get("concept_order", [])
        facts = statement.get("facts", [])

        periods_seen: set[str] = set()
        periods: list[str] = []
        facts_by_concept_and_period: dict[tuple[str, str], dict[str, Any]] = {}
        labels_by_concept: dict[str, str] = {}

        for fact in facts:
            if not is_consolidated_fact(fact):
                continue

            period_key = build_period_key((fact.get("context") or {}).get("period") or {})
            if period_key not in periods_seen:
                periods_seen.add(period_key)
                periods.append(period_key)

            concept = fact["concept"]
            labels_by_concept.setdefault(concept, fact["label"])
            facts_by_concept_and_period.setdefault(
                (concept, period_key),
                simplify_fact(fact),
            )

        line_items: list[dict[str, Any]] = []
        for concept in concept_order:
            values_by_period: dict[str, dict[str, Any]] = {}
            for period_key in periods:
                simplified_fact = facts_by_concept_and_period.get((concept, period_key))
                if simplified_fact:
                    values_by_period[period_key] = {
                        "value": simplified_fact["value"],
                        "unit": simplified_fact["unit"],
                        "decimals": simplified_fact["decimals"],
                        "context_id": simplified_fact["context_id"],
                    }

            if not values_by_period:
                continue

            line_items.append(
                {
                    "concept": concept,
                    "label": labels_by_concept.get(concept, concept),
                    "values": values_by_period,
                }
            )

        readable[statement_key] = {
            "role_definition": statement["role_definition"],
            "periods": sorted(periods, key=period_sort_key),
            "line_item_count": len(line_items),
            "line_items": line_items,
        }

    return readable


def build_readable_numeric_disclosures(
    numeric_disclosures: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    readable_roles: list[dict[str, Any]] = []

    for role in numeric_disclosures:
        concept_order = role.get("concept_order", [])
        facts = role.get("numeric_facts") or role.get("facts", [])

        periods_seen: set[str] = set()
        periods: list[str] = []
        facts_by_concept_and_period: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        labels_by_concept: dict[str, str] = {}

        for fact in facts:
            period_key = build_period_key((fact.get("context") or {}).get("period") or {})
            if period_key not in periods_seen:
                periods_seen.add(period_key)
                periods.append(period_key)

            concept = fact["concept"]
            labels_by_concept.setdefault(concept, fact["label"])
            facts_by_concept_and_period[(concept, period_key)].append(simplify_fact(fact))

        line_items: list[dict[str, Any]] = []
        for concept in concept_order:
            values_by_period: dict[str, Any] = {}
            for period_key in periods:
                simplified_facts = facts_by_concept_and_period.get((concept, period_key))
                if not simplified_facts:
                    continue

                if len(simplified_facts) == 1:
                    fact = simplified_facts[0]
                    values_by_period[period_key] = {
                        "value": fact["value"],
                        "unit": fact["unit"],
                        "decimals": fact["decimals"],
                        "context_id": fact["context_id"],
                        "dimensions": fact["dimensions"],
                    }
                else:
                    values_by_period[period_key] = [
                        {
                            "value": fact["value"],
                            "unit": fact["unit"],
                            "decimals": fact["decimals"],
                            "context_id": fact["context_id"],
                            "period": fact["period"],
                            "dimensions": fact["dimensions"],
                        }
                        for fact in simplified_facts
                    ]

            if not values_by_period:
                continue

            line_items.append(
                {
                    "concept": concept,
                    "label": labels_by_concept.get(concept, concept),
                    "values": values_by_period,
                }
            )

        readable_roles.append(
            {
                "role_uri": role["role_uri"],
                "role_definition": role["role_definition"],
                "role_kind": role["role_kind"],
                "periods": sorted(periods, key=period_sort_key),
                "line_item_count": len(line_items),
                "line_items": line_items,
            }
        )

    return readable_roles


def get_item8_xbrl_facts(
    cik: str,
    accession_number: str,
    mode: str = "statements_and_notes",
    user_agent: str = USER_AGENT,
) -> dict[str, Any]:
    client = SecClient(user_agent=user_agent)
    filing_paths = build_filing_paths(client, cik, accession_number)

    schema_root = parse_xml(read_resource_bytes(client, filing_paths.schema_url))
    label_root = parse_xml(read_resource_bytes(client, filing_paths.label_url))
    presentation_root = parse_xml(read_resource_bytes(client, filing_paths.presentation_url))
    instance_root = parse_xml(read_resource_bytes(client, filing_paths.instance_url))

    role_definitions = parse_role_definitions(schema_root)
    labels = parse_labels(label_root)
    roles = parse_presentation_roles(presentation_root, role_definitions, labels)
    roles = filter_roles_by_mode(roles, mode)
    contexts = parse_contexts(instance_root)
    units = parse_units(instance_root)
    all_facts = parse_facts(instance_root, contexts, units, labels)

    facts_by_concept: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in all_facts:
        facts_by_concept[fact["concept"]].append(fact)

    role_outputs: list[dict[str, Any]] = []
    for role in roles:
        concept_set = set(role["concepts"])
        role_facts: list[dict[str, Any]] = []
        for concept_name in sorted(concept_set):
            role_facts.extend(facts_by_concept.get(concept_name, []))

        role_facts = dedupe_role_facts(role_facts)

        if not role_facts:
            continue

        role_outputs.append(
            {
                "role_uri": role["role_uri"],
                "role_definition": role["role_definition"],
                "role_kind": role["role_kind"],
                "concept_order": unique_preserve_order(role["concepts"]),
                "fact_count": len(role_facts),
                "facts": role_facts,
            }
        )

    statements = build_statement_map(role_outputs)
    other_financial_roles = build_other_financial_roles(role_outputs)
    text_disclosures, numeric_disclosures = split_other_financial_roles(other_financial_roles)
    readable_statements = build_readable_statements(statements)
    readable_numeric_disclosures = build_readable_numeric_disclosures(numeric_disclosures)

    return {
        "filing_info": {
            "cik": filing_paths.cik,
            "accession_number": filing_paths.accession_number,
            "company_name": filing_paths.company_name,
            "filing_dir_url": filing_paths.filing_dir_url,
        },
        "sources": {
            "schema_url": filing_paths.schema_url,
            "presentation_url": filing_paths.presentation_url,
            "label_url": filing_paths.label_url,
            "instance_url": filing_paths.instance_url,
        },
        "summary": {
            "mode": mode,
            "role_count": len(role_outputs),
            "fact_count": sum(role["fact_count"] for role in role_outputs),
            "statement_count": len(statements),
            "other_role_count": len(other_financial_roles),
            "text_disclosure_count": len(text_disclosures),
            "numeric_disclosure_count": len(numeric_disclosures),
            "readable_statement_count": len(readable_statements),
            "readable_numeric_disclosure_count": len(readable_numeric_disclosures),
        },
        "text_disclosures": text_disclosures,
        "readable_statements": readable_statements,
        "readable_numeric_disclosures": readable_numeric_disclosures,
    }
