from fastapi.testclient import TestClient
from backend.api.app import app

def test_existing_market_pulse_page_is_upgraded():
 text=TestClient(app).get('/market-pulse.html').text
 script=TestClient(app).get('/industry-radar.js').text
 assert 'data-level="2" class="active"' in text
 assert 'industry-radar.js' in text and 'industry_score_v1' in text
 assert '<th>层级</th>' not in text
 assert 'colspan="11"' in text
 assert '<b>数据日期：</b><span id="industry-trade-date">—</span>' in text
 assert 'textContent=d.trade_date||DASH' in script
 assert 'id="industry-scroll-top"' in text and 'id="industry-table-wrap"' in text
 assert 'topScroll.onscroll' in script and 'tableWrap.onscroll' in script
