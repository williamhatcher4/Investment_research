"""Run the real investor-research MCP server on fictional, offline data.

Only the outermost network calls are replaced (Tavily SDK, FMP HTTP GET,
SEC EDGAR HTTP GET). Everything else — tool logic, response mapping, the
vault, the SQLite index and the MCP protocol layer — is the production
code. No API keys or network access needed.

    python demo/demo_server.py          # speaks MCP over stdio
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_vault = Path(tempfile.mkdtemp(prefix="investor-research-demo-")) / "vault"
os.environ.update({
    "RESEARCH_PROVIDER": "tavily",
    "TAVILY_API_KEY": "demo-not-a-real-key",
    "FMP_API_KEY": "demo-not-a-real-key",
    "VAULT_ROOT": str(_vault),
    "ENABLE_SQLITE": "true",
    "LOG_LEVEL": os.environ.get("LOG_LEVEL", "WARNING"),
})

from demo import fake_data  # noqa: E402
from investor_research.clients import fmp, sec_edgar, tavily  # noqa: E402
from investor_research.config import get_settings  # noqa: E402
from investor_research.models import FundamentalsSnapshot  # noqa: E402
from investor_research.vault.database import reindex_vault  # noqa: E402
from investor_research.vault.paths import create_company_folder  # noqa: E402
from investor_research.vault.snapshots import write_parsed_json  # noqa: E402

# Swap the network boundary for fictional data.
tavily._build_default_client = lambda settings: fake_data.FakeTavilySDK()
fmp.FmpClient._get = lambda self, path, *, params=None, first=False: (
    (lambda d: (d[0] if d else None) if first and isinstance(d, list) else d)(
        fake_data.fmp_response(path, params)
    )
)
sec_edgar.SecEdgarClient._safe_json = lambda self, url: fake_data.edgar_response(url)


def seed_vault() -> None:
    settings = get_settings()
    create_company_folder(settings, fake_data.SUBJECT,
                          company_name=fake_data.COMPANIES[fake_data.SUBJECT]["name"],
                          sector="Industrials")
    write_parsed_json(settings, FundamentalsSnapshot(**fake_data.vault_snapshot_kwargs()))
    reindex_vault(settings)


seed_vault()

import server  # noqa: E402  (imports after patching so tools see the fakes)

if __name__ == "__main__":
    server.mcp.run()
