"""The CLI must not enter the native libsql path that stalled a live catch-up."""
from knowledge import ingest


def test_cli_fresh_connection_never_opens_native_libsql(monkeypatch):
    import db.client
    monkeypatch.setenv("TURSO_DB_URL", "libsql://knowledge-test.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "fake-test-token")
    def native_is_forbidden():
        raise AssertionError("knowledge CLI entered unbounded native libsql")
    monkeypatch.setattr(db.client, "get_db", native_is_forbidden)
    first = ingest._fresh_db()
    second = ingest._fresh_db()
    assert first is not second
