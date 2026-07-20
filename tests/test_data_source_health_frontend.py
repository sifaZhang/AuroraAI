from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_health_page_and_javascript_interactions_exist():
    html = (ROOT / "frontend" / "data-source-health.html").read_text(encoding="utf-8")
    script = (ROOT / "frontend" / "data-source-health.js").read_text(encoding="utf-8")
    assert "数据源健康状态" in html
    assert 'id="check-all"' in html
    assert "/api/data-source-health/check" in script
    assert 'data-check="${esc(item.source)}"' in script
    assert "检查中…" in script
    assert "检查超时，请稍后重试。" in script
    assert "showError" in script


def test_existing_pages_link_to_health_without_changing_business_scripts():
    for name in ("index.html", "expectation-gap.html"):
        html = (ROOT / "frontend" / name).read_text(encoding="utf-8")
        assert 'href="/data-source-health"' in html
