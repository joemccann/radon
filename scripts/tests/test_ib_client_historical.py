from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from clients.ib_client import IBClient


def test_head_timestamp_uses_ib_request_and_normalizes_response():
    client = IBClient.__new__(IBClient)
    client._ib = MagicMock()
    expected = datetime(2005, 1, 3, 14, 30, tzinfo=timezone.utc)
    client._ib.isConnected.return_value = True
    client._ib.reqHeadTimeStamp.return_value = expected
    contract = MagicMock()

    assert client.get_head_timestamp(contract, what_to_show="MIDPOINT", use_rth=False) == expected
    client._ib.reqHeadTimeStamp.assert_called_once_with(
        contract, whatToShow="MIDPOINT", useRTH=False, formatDate=2
    )
