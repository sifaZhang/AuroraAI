from fastapi.testclient import TestClient

from backend.api.app import app


PAGES = {
    "/": ("index.html", "dividend"),
    "/market-pulse.html": ("market-pulse.html", "market"),
    "/dividend/universe": ("dividend-universe.html", "dividend"),
    "/expectation-gap": ("expectation-gap.html", "expectation"),
    "/first-limit": ("first-limit.html", "strategy"),
    "/data-source-health": ("data-source-health.html", "health"),
}


def test_primary_pages_share_the_reorganized_navigation():
    client = TestClient(app)
    for route, (_, active) in PAGES.items():
        response = client.get(route)
        assert response.status_code == 200
        text = response.text
        assert 'aria-label="主导航"' in text
        assert 'href="/market-pulse.html">Market Pulse' in text
        assert '<summary>分红</summary>' in text
        assert 'href="/dividend/universe">A股历史分红' in text
        assert 'href="/">最近分红' in text
        expectation_href = '/expectation-gap?nav=20260809'
        assert f'href="{expectation_href}">预期差' in text
        assert '<summary>战法</summary>' in text
        assert 'href="/first-limit">首板回调战法' in text
        assert 'href="/data-source-health">数据源状态' in text
        if active == "market":
            assert '<a class="active" href="/market-pulse.html">Market Pulse' in text
        elif active == "dividend":
            assert '<details class="nav-dropdown active"><summary>分红</summary>' in text
        elif active == "expectation":
            assert '<a class="active" href="/expectation-gap?nav=20260809">预期差' in text
        elif active == "strategy":
            assert '<details class="nav-dropdown active"><summary>战法</summary>' in text
        else:
            assert '<a class="active" href="/data-source-health">数据源状态' in text


def test_navigation_destinations_are_existing_pages():
    client = TestClient(app)
    for route in PAGES:
        response = client.get(route)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
