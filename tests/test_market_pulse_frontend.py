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
    assert "Market Pulse" in page and "板块雷达" in page
    for element_id in ("refresh-sectors", "sector-rows", "source-statuses", "refresh-job-card", "job-progress", "sector-search", "level-filter", "source-filter", "reset-sector-filters"):
        assert f'id="{element_id}"' in page
    assert page.count('class="sort-button') == 7
    for filename in ("index.html", "expectation-gap.html", "data-source-health.html"):
        content = (ROOT / "frontend" / filename).read_text(encoding="utf-8")
        assert 'href="/market-pulse.html"' in content


def test_market_pulse_frontend_scope_and_safety():
    script = (ROOT / "frontend" / "market-pulse.js").read_text(encoding="utf-8")
    page = (ROOT / "frontend" / "market-pulse.html").read_text(encoding="utf-8")
    assert "`/api/market-pulse/sectors?source=${encodeURIComponent(state.source)}`" in script
    assert 'fetchJson("/api/data-source-health"' in script
    assert 'JSON.stringify({source: "sw_l1"})' in script
    assert 'source: "all"' not in script
    assert "/api/market-pulse/refresh/${jobId}" in script
    assert "Promise.allSettled" in script
    assert "textContent" in script and ".innerHTML" not in script
    for field in ("total_score", "trend_score", "breadth_score", "above_ma20", "volume_expansion", "lookahead_warning", "_ratio", "_numerator", "_valid_count"):
        assert field in script
    assert "近似结果 ⚠" in script and "not_calculated" in script
    assert "总分" in page and "Breadth" in page and "指标详情" in page
    assert all(library not in page.lower() for library in ("react", "vue", "bootstrap", "tailwind", "jquery"))
