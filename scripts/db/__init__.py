"""libSQL helpers — embedded-replica client and write helpers.

Mirrors scripts/db/writer.js. Schedulers should:

    from scripts.db.client import get_db
    from scripts.db.writer import upsert_cri_snapshot, record_service_health
"""
