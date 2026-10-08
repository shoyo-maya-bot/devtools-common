"""HttpClient: path に「?」があるときの params のつなぎ方. 通信はしない（opener を差し替える）."""

from __future__ import annotations

import unittest

from devtools_common.httpclient import HttpClient


class _Resp:
    status = 200
    headers: dict[str, str] = {}

    def read(self):
        return b"{}"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None


class HttpParamsTest(unittest.TestCase):
    def _url(self, path, params):
        seen = []

        def opener(req, timeout):
            seen.append(req.full_url)
            return _Resp()

        HttpClient("https://api.example.test/v1", opener=opener, rate_per_sec=1000).get(path, params=params)
        return seen[0]

    def test_params_appended_to_existing_query(self):
        self.assertEqual(self._url("items?page=2", {"q": "x"}), "https://api.example.test/v1/items?page=2&q=x")

    def test_params_without_query(self):
        self.assertEqual(
            self._url("items", {"q": "a b", "t": ["1", "2"]}), "https://api.example.test/v1/items?q=a+b&t=1&t=2"
        )

    def test_existing_query_kept_and_fragment_dropped_from_query(self):
        self.assertEqual(self._url("items?", {"q": "x"}), "https://api.example.test/v1/items?q=x")


if __name__ == "__main__":
    unittest.main()
