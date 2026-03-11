"""Tests for CRI scanner IB client-id pools."""

from cri_scan import CRI_IB_HISTORY_CLIENT_IDS, CRI_IB_QUOTE_CLIENT_IDS


class TestCRIClientIds:
    """Verify CRI uses dedicated, non-conflicting client-id pools."""

    def test_history_client_ids_are_non_zero_and_unique(self):
        assert len(set(CRI_IB_HISTORY_CLIENT_IDS)) == len(CRI_IB_HISTORY_CLIENT_IDS)
        assert all(client_id != 0 for client_id in CRI_IB_HISTORY_CLIENT_IDS)

    def test_quote_client_ids_are_non_zero_and_unique(self):
        assert len(set(CRI_IB_QUOTE_CLIENT_IDS)) == len(CRI_IB_QUOTE_CLIENT_IDS)
        assert all(client_id != 0 for client_id in CRI_IB_QUOTE_CLIENT_IDS)

    def test_pools_do_not_collide_with_known_script_ids(self):
        known_ids = {
            0,    # TWS default / ib_client.py default
            18,   # evaluate.py
            26,   # ib_place_order.py
            55,   # portfolio_report.py
            100,  # ib_realtime_server.js
            200,  # test_ib_realtime.py
        }
        assert known_ids.isdisjoint(CRI_IB_HISTORY_CLIENT_IDS)
        assert known_ids.isdisjoint(CRI_IB_QUOTE_CLIENT_IDS)
