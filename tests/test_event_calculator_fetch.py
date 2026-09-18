"""活动计算器取数逻辑测试：双源回退与风控冷却。"""

import unittest
from unittest.mock import Mock, patch

import requests

from module.webui import event_calculator as ec


def _http_error(status: int) -> requests.HTTPError:
    response = Mock()
    response.status_code = status
    return requests.HTTPError(f"{status} Server Error", response=response)


class TestFetchWikiText(unittest.TestCase):
    def test_raw_failure_falls_back_to_api(self):
        api_payload = {"parse": {"wikitext": "当前活动：[[某活动]]\n{|id=\"ECALC\"\n|A1||30||\n|}"}}
        ok = Mock()
        ok.raise_for_status.return_value = None
        ok.json.return_value = api_payload

        with patch.object(ec.requests, "get") as get, patch.object(ec, "WIKI_API_URL", "https://api"):
            get.side_effect = [_http_error(567), ok]
            text, url = ec._fetch_wiki_text()

        self.assertEqual(url, "https://api")
        self.assertIn("某活动", text)
        self.assertEqual(get.call_count, 2)

    def test_both_sources_failed_reports_status(self):
        with patch.object(ec.requests, "get", side_effect=_http_error(567)):
            with self.assertRaises(RuntimeError) as ctx:
                ec._fetch_wiki_text()

        message = str(ctx.exception)
        self.assertIn("567", message)
        self.assertIn("action=raw", message)
        self.assertIn("api.php", message)


class TestFetchCooldown(unittest.TestCase):
    def setUp(self):
        ec._failure_state["at"] = 0.0
        ec._failure_state["message"] = ""

    def tearDown(self):
        ec._failure_state["at"] = 0.0
        ec._failure_state["message"] = ""

    def test_cooldown_skips_network(self):
        ec._record_fetch_failure("Wiki 请求失败（action=raw 返回 HTTP 567）")

        with patch.object(ec, "_read_cache", return_value={}), \
                patch.object(ec.requests, "get") as get:
            result = ec.load_event_calculator(force_refresh=True)

        get.assert_not_called()
        self.assertFalse(result["from_cache"])
        self.assertIn("秒后再试", result["error"])

    def test_cooldown_falls_back_to_cache_when_available(self):
        ec._record_fetch_failure("Wiki 请求失败（action=raw 返回 HTTP 567）")
        cached = {
            "cache_version": ec.CACHE_VERSION,
            "event_name": "缓存活动",
            "shop_items": [{"name": "x", "price": 1, "quantity": 1}],
        }

        with patch.object(ec, "_read_cache", return_value=cached), \
                patch.object(ec.requests, "get") as get:
            result = ec.load_event_calculator(force_refresh=True)

        get.assert_not_called()
        self.assertTrue(result["from_cache"])
        self.assertEqual(result["event_name"], "缓存活动")
        self.assertIn("567", result["error"])


if __name__ == "__main__":
    unittest.main()
