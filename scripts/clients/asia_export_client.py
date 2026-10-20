"""Official MOF Taiwan CSV and KCS Korea monthly total exports, without auth."""
from __future__ import annotations

import re
import time

import requests

TAIWAN_URL = "https://web02.mof.gov.tw/njswww/webMain.aspx?codlst0=11001001&compmode=00&cycle=41&fld0=1&funid=i8101&kind=21&outkind=1&outmode=12&sys=220&type=4&utf=1&ym=9000"
KOREA_URL = "https://www.tradedata.go.kr/cts/hmpgEng/retrieveTradeEngAll.do"
_RETRYABLE = {429, 500, 502, 503, 504}
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class AsiaExportClientError(RuntimeError):
    """An official source failed; callers must retain their last good data."""


class AsiaExportClient:
    def __init__(self, *, timeout=35, max_retries=2, backoff_factor=1.0):
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "radon/2.0"})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.session.close()

    def _request(self, method, url, **kwargs):
        for attempt in range(self.max_retries + 1):
            try:
                with self.session.request(method, url, timeout=(10, self.timeout),
                                          stream=True, **kwargs) as response:
                    if response.status_code == 304 and method == "GET":
                        return None, response.headers.get("Last-Modified")
                    if response.status_code != 200:
                        if response.status_code in _RETRYABLE and attempt < self.max_retries:
                            time.sleep(self.backoff_factor * 2 ** attempt)
                            continue
                        raise AsiaExportClientError(f"HTTP {response.status_code} fetching {url}")
                    body = bytearray()
                    for chunk in response.iter_content(chunk_size=65536):
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise AsiaExportClientError("Official export response exceeds size bound")
                    if not body:
                        raise AsiaExportClientError("Official export response is empty")
                    return bytes(body), response.headers.get("Last-Modified")
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt == self.max_retries:
                    raise AsiaExportClientError("Official export connection retries exhausted") from exc
                time.sleep(self.backoff_factor * 2 ** attempt)

    def fetch_taiwan(self, if_modified_since=None):
        headers = {"If-Modified-Since": if_modified_since} if if_modified_since else {}
        body, modified = self._request("GET", TAIWAN_URL, headers=headers)
        return body, modified if body is not None else (modified or if_modified_since)

    def fetch_korea(self, end_month):
        if not re.fullmatch(r"\d{4}(0[1-9]|1[0-2])", end_month):
            raise ValueError("Korea end_month must be YYYYMM")
        body, _ = self._request("POST", KOREA_URL, data={
            "priodKind": "MON", "priodFr": "200001", "priodTo": end_month,
            "langTpcd": "ENG", "ttwgTpcd": "1000", "selectPaging": "1",
            "showPagingLine": "1000", "sortColumn": "", "sortOrder": "",
        })
        return body
