"""MedScout public-evidence MCP server (Lec 5 "The shop MCP server").

Exposes the read-only public-source tools (openFDA, ClinicalTrials.gov,
PubMed) over MCP so any MCP-capable host can reuse them. The MedScout
runtime uses it with `--tool-transport mcp`: it discovers tools with
tools/list, keeps only the curated allowlist, and invokes them with tools/call.

Run standalone:  python medscout/mcp_server.py --backend live
                 python medscout/mcp_server.py --backend mirror --mirror data/cases/nordleaf/public_mirror
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("FASTMCP_LOG_LEVEL", "WARNING")   # keep the host's terminal quiet

from fastmcp import FastMCP  # noqa: E402
from fastmcp.exceptions import ToolError as MCPToolError  # noqa: E402

from medscout.tools import clinicaltrials as ct  # noqa: E402
from medscout.tools import openfda, pubmed  # noqa: E402
from medscout.tools.base import ToolContext, ToolError  # noqa: E402

mcp = FastMCP("MedScout public evidence")
CTX = ToolContext(backend="live", mirror_dir=None)


def _run(fn, **kwargs):
    kwargs = {k: v for k, v in kwargs.items() if v not in (None, "")}
    try:
        return fn(CTX, **kwargs)
    except ToolError as exc:
        # prefix the failure class so the host can apply its retry policy
        raise MCPToolError(f"{exc.kind}: {exc.message}") from exc


@mcp.tool
def fda_510k_search(applicant: str = "", device_name: str = "", k_number: str = "",
                    product_code: str = "", limit: int = 5) -> dict:
    """Search FDA 510(k) premarket notifications (clearances)."""
    return _run(openfda.fda_510k_search, applicant=applicant, device_name=device_name,
                k_number=k_number, product_code=product_code, limit=limit)


@mcp.tool
def fda_pma_search(applicant: str = "", trade_name: str = "", pma_number: str = "",
                   product_code: str = "", limit: int = 5) -> dict:
    """Search FDA Premarket Approval (PMA) records."""
    return _run(openfda.fda_pma_search, applicant=applicant, trade_name=trade_name,
                pma_number=pma_number, product_code=product_code, limit=limit)


@mcp.tool
def fda_classification_lookup(product_code: str = "", device_name: str = "", limit: int = 3) -> dict:
    """Look up an FDA product code: device type, class and regulation number."""
    return _run(openfda.fda_classification_lookup, product_code=product_code, device_name=device_name, limit=limit)


@mcp.tool
def clinicaltrials_search(query: str = "", sponsor: str = "", intervention: str = "", condition: str = "",
                          nct_id: str = "", limit: int = 5) -> dict:
    """Search ClinicalTrials.gov registrations."""
    return _run(ct.clinicaltrials_search, query=query, sponsor=sponsor, intervention=intervention,
                condition=condition, nct_id=nct_id, limit=limit)


@mcp.tool
def pubmed_search(query: str, limit: int = 5) -> dict:
    """Search PubMed for publications."""
    return _run(pubmed.pubmed_search, query=query, limit=limit)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["live", "mirror"], default="live")
    ap.add_argument("--mirror", default="")
    ap.add_argument("--timeout", type=float, default=10.0)
    a = ap.parse_args(argv)
    CTX.backend = a.backend
    CTX.mirror_dir = Path(a.mirror) if a.mirror else None
    CTX.timeout_s = a.timeout
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
