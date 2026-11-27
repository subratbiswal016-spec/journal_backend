"""Unit tests for IPO company introduction, Screener profile parsing, and NSE bid breakdown."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend import ipo


def test_ipo_detail_parses_screener_and_nse():
    ipo._detail_cache.clear()
    search_resp = MagicMock(status_code=200)
    search_resp.json.return_value = [
        {"id": 1286300, "name": "Foodlink F&B Holdings (India) Ltd", "url": "/company/id/1286300/consolidated/"}
    ]
    page_resp = MagicMock(status_code=200)
    page_resp.text = """
    <html><body>
      <div class="company-profile"><p>Foodlink is a food & beverage catering company. [1]</p></div>
      <a title="Sector">Consumer Services</a>
      <ul id="top-ratios">
        <li><span class="name">Market Cap</span><span class="nowrap value">₹ 1,191 Cr.</span></li>
        <li><span class="name">Current Price</span><span class="nowrap value">₹</span></li>
        <li><span class="name">ROE</span><span class="nowrap value">17.9 %</span></li>
      </ul>
      <div class="pros"><ul><li>Strong revenue growth</li></ul></div>
      <div class="cons"><ul><li>High valuation multiple</li></ul></div>
    </body></html>
    """
    nse_sess = MagicMock()
    nse_det = MagicMock(status_code=200)
    nse_det.json.return_value = {
        "bidDetails": [
            {"category": "QIBs", "noOfTime": "1.01", "srNo": "1"},
            {"category": "FIIs", "noOfTime": "0.5", "srNo": "1(a)"},
            {"category": "Retail (RIIs)", "noOfTime": "2.45", "srNo": "3"},
        ]
    }
    nse_sess.get.return_value = nse_det

    def _fake_get(url, **_kw):
        if "company/search" in url:
            return search_resp
        return page_resp

    with patch("backend.ipo.requests.get", side_effect=_fake_get), \
         patch("backend.ipo._session_get", return_value=nse_sess):
        res = ipo.detail(symbol="FOODLINK", name="Foodlink F&B Holdings (India) Limited")

    assert res["about"] == "Foodlink is a food & beverage catering company."
    assert res["sector"] == "Consumer Services"
    assert [r["name"] for r in res["ratios"]] == ["Market Cap", "ROE"]
    assert res["pros"] == ["Strong revenue growth"]
    assert res["cons"] == ["High valuation multiple"]
    assert res["bids"] == [{"category": "QIBs", "times": 1.01}, {"category": "Retail (RIIs)", "times": 2.45}]
