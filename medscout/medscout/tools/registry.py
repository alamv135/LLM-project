"""Tool registry: the explicit allowlist of operations (Lec 5 "Registration and dispatch")."""

from __future__ import annotations

from .base import ToolSpec, envelope
from . import clinicaltrials as ct
from . import database, local, openfda, pubmed

# Tools served by the public-evidence MCP server when --tool-transport mcp is used.
PUBLIC_SOURCE_TOOLS = {"fda_510k_search", "fda_pma_search", "fda_classification_lookup",
                       "clinicaltrials_search", "pubmed_search"}

_GENERIC = envelope({"type": "object"})


DATABASE_TOOLS = {"db_list_tables", "db_describe_table", "db_query"}


def database_specs() -> list[ToolSpec]:
    common = dict(output_schema=database.OUTPUT, source_label="Local database", independent=False,
                  record_id_field="_row", url_template="localdb://{_db}/{_row}")
    return [
        ToolSpec(name="db_list_tables", description="List the tables in the user's own SQLite database "
                 "(names, row counts, columns). Internal data: context, not independent evidence.",
                 input_schema=database.LIST_INPUT, handler=database.db_list_tables, **common),
        ToolSpec(name="db_describe_table", description="Columns, keys and 3 sample rows of one table in the "
                 "user's database. Use before writing a query.",
                 input_schema=database.DESCRIBE_INPUT, handler=database.db_describe_table, **common),
        ToolSpec(name="db_query", description="Run ONE read-only SQLite SELECT on the user's database "
                 "(max 200 rows; use WHERE / GROUP BY / LIMIT).",
                 input_schema=database.QUERY_INPUT, handler=database.db_query, **common),
    ]


def build_specs(include_database: bool = False) -> dict[str, ToolSpec]:
    specs = [
        ToolSpec(
            name="search_company_docs",
            description="Search the company's own materials (deck, whitepaper, press releases). "
                        "Evidence from here is COMPANY-REPORTED, never independent support.",
            input_schema=local.SEARCH_DOCS_INPUT, output_schema=local.SEARCH_DOCS_OUTPUT,
            handler=local.search_company_docs, source_label="Company material",
            independent=False, record_id_field="chunk_id"),
        ToolSpec(
            name="fda_510k_search",
            description="Search FDA 510(k) premarket notifications (clearances) by applicant, device name, "
                        "K-number or product code. Use for claims of 'FDA cleared/approved' and to find "
                        "devices sharing a product code (comparable products).",
            input_schema=openfda.FDA_510K_INPUT, output_schema=openfda.FDA_510K_OUTPUT,
            handler=openfda.fda_510k_search, source_label="openFDA 510(k)", record_id_field="k_number",
            url_template="https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpmn/pmn.cfm?ID={k_number}"),
        ToolSpec(
            name="fda_pma_search",
            description="Search FDA Premarket Approval (PMA) records. A claim of 'FDA approval' for a "
                        "device should normally be backed by a PMA, not a 510(k).",
            input_schema=openfda.FDA_PMA_INPUT, output_schema=openfda.FDA_PMA_OUTPUT,
            handler=openfda.fda_pma_search, source_label="openFDA PMA", record_id_field="pma_number",
            url_template="https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpma/pma.cfm?id={pma_number}"),
        ToolSpec(
            name="fda_classification_lookup",
            description="Look up an FDA product code: device type, device class and regulation number.",
            input_schema=openfda.FDA_CLASS_INPUT, output_schema=openfda.FDA_CLASS_OUTPUT,
            handler=openfda.fda_classification_lookup, source_label="openFDA classification",
            record_id_field="product_code",
            url_template="https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPCD/classification.cfm?id={product_code}"),
        ToolSpec(
            name="clinicaltrials_search",
            description="Search ClinicalTrials.gov registrations by sponsor, intervention, condition, "
                        "free text or NCT id. Returns status, enrollment and dates.",
            input_schema=ct.CT_INPUT, output_schema=ct.CT_OUTPUT,
            handler=ct.clinicaltrials_search, source_label="ClinicalTrials.gov", record_id_field="nct_id",
            url_template="https://clinicaltrials.gov/study/{nct_id}"),
        ToolSpec(
            name="pubmed_search",
            description="Search PubMed for peer-reviewed publications (title, journal, date, DOI).",
            input_schema=pubmed.PUBMED_INPUT, output_schema=pubmed.PUBMED_OUTPUT,
            handler=pubmed.pubmed_search, source_label="PubMed", record_id_field="pmid",
            url_template="https://pubmed.ncbi.nlm.nih.gov/{pmid}/"),
        ToolSpec(
            name="read_skill_reference",
            description="Read a reference file belonging to a skill (e.g. regulatory definitions).",
            input_schema=local.READ_REF_INPUT, output_schema=_GENERIC,
            handler=local.read_skill_reference, source_label="Skill reference", registers_evidence=False),
        ToolSpec(
            name="get_evidence",
            description="Recover the full stored record for an evidence id that was summarised earlier.",
            input_schema=local.GET_EVIDENCE_INPUT, output_schema=_GENERIC,
            handler=local.get_evidence, source_label="Evidence store", registers_evidence=False),
    ]
    if include_database:
        specs += database_specs()
    return {s.name: s for s in specs}


def catalog_text(specs: dict[str, ToolSpec]) -> str:
    """Compact, deterministic tool catalog for the stable prompt prefix."""
    import json
    lines = []
    for name in sorted(specs):
        s = specs[name]
        props = s.input_schema.get("properties", {})
        args = ", ".join(
            f"{k}: {v.get('type', 'any')}" + (f" /{v['pattern']}/" if "pattern" in v else "")
            for k, v in props.items())
        req = s.input_schema.get("required") or [
            "one of " + "|".join(a["required"][0] for a in s.input_schema.get("anyOf", []))]
        lines.append(f"- {name}({args})\n  required: {json.dumps(req)}\n  {s.description}")
    return "\n".join(lines)
