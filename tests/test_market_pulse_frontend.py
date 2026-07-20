import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_market_pulse_frontend_with_mocked_network():
    node = shutil.which("node")
    assert node, "Node.js is required for the native JavaScript frontend test"
    result = subprocess.run(
        [node, str(ROOT / "tests" / "js" / "market_pulse_frontend.test.js")],
        cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "frontend mock tests passed" in result.stdout


def test_market_pulse_page_contract_and_navigation():
    page = (ROOT / "frontend" / "market-pulse.html").read_text(encoding="utf-8")
    assert "Market Pulse" in page and "板块脉搏" in page
    for element_id in ("refresh-sectors", "sector-rows", "source-statuses", "refresh-job-card", "job-progress"):
        assert f'id="{element_id}"' in page
    for filename in ("index.html", "expectation-gap.html", "data-source-health.html"):
        content = (ROOT / "frontend" / filename).read_text(encoding="utf-8")
        assert 'href="/market-pulse.html"' in content


def test_market_pulse_frontend_scope_and_safety():
    script = (ROOT / "frontend" / "market-pulse.js").read_text(encoding="utf-8")
    page = (ROOT / "frontend" / "market-pulse.html").read_text(encoding="utf-8")
    assert 'fetchJson("/api/market-pulse/sectors"' in script
    assert 'fetchJson("/api/data-source-health"' in script
    assert 'JSON.stringify({source: "sw_l1"})' in script
    assert 'source: "all"' not in script
    assert "/api/market-pulse/refresh/${jobId}" in script
    assert "Promise.allSettled" in script
    assert "textContent" in script and ".innerHTML" not in script
    assert "Breadth =" not in page and "Composite =" not in page
    assert all(library not in page.lower() for library in ("react", "vue", "bootstrap", "tailwind", "jquery"))
