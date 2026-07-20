import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_market_pulse_frontend_with_mocked_network():
    node = shutil.which("node")
    assert node, "Node.js is required for the native JavaScript frontend test"
    result = subprocess.run(
        [node, str(ROOT / "tests" / "js" / "market_pulse_frontend.test.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "frontend mock tests passed" in result.stdout


def test_market_pulse_page_contract_and_home_entry():
    page = (ROOT / "frontend" / "market-pulse.html").read_text(encoding="utf-8")
    home = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "Market Pulse" in page
    assert 'id="refresh-sectors"' in page
    assert 'id="sector-rows"' in page
    assert 'id="source-statuses"' in page
    assert 'href="/market-pulse.html"' in home
