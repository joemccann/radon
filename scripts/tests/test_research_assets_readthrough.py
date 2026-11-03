"""Read-through for pruned research assets: local miss -> B2 GET -> verify -> serve.

The asset archive prunes cold local copies, so every reader goes through
``research.assets.read_asset``. These tests assert the request on the wire
(bucket and exact object key) and that nothing is requested on a local hit.
"""
import hashlib
import io
import os

import pytest

from research import assets

ENV = {
    "RADON_ARCHIVE_S3_ENDPOINT": "s3.us-west-004.backblazeb2.com",
    "RADON_ARCHIVE_S3_BUCKET": "radon-archive",
    "RADON_ARCHIVE_S3_ACCESS_KEY_ID": "id",
    "RADON_ARCHIVE_S3_SECRET_ACCESS_KEY": "secret",
    # The portfolio prefix must never leak into research keys.
    "RADON_ARCHIVE_S3_PREFIX": "portfolio_snapshots/",
}
PDF = b"%PDF-1.7 archived research document"
NAME = hashlib.sha256(PDF).hexdigest() + ".pdf"


class NotFound(Exception):
    def __init__(self):
        super().__init__("Not Found")
        self.response = {"Error": {"Code": "NoSuchKey"}}


class Body(io.BytesIO):
    closed_by_reader = False

    def close(self):
        Body.closed_by_reader = True
        super().close()


class FakeS3:
    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.requests = []

    def get_object(self, **kwargs):
        self.requests.append(kwargs)
        data = self.objects.get((kwargs["Bucket"], kwargs["Key"]))
        if data is None:
            raise NotFound()
        return {"ContentLength": len(data), "Body": Body(data)}


@pytest.fixture
def research(tmp_path, monkeypatch):
    root = tmp_path / "research"
    (root / "assets").mkdir(parents=True)
    monkeypatch.setenv("RADON_RESEARCH_DIR", str(root))
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("RADON_RESEARCH_ARCHIVE_PREFIX", raising=False)
    return root


def _client(monkeypatch, s3):
    built = []

    def factory(cfg):
        built.append(cfg)
        return s3

    monkeypatch.setattr(assets, "_archive_client", factory)
    return built


class TestReadThrough:
    def test_local_hit_never_touches_b2(self, research, monkeypatch):
        (research / "assets" / NAME).write_bytes(PDF)
        s3 = FakeS3()
        built = _client(monkeypatch, s3)
        assert assets.read_asset(NAME) == PDF
        assert s3.requests == [] and built == []

    def test_miss_requests_the_exact_key_verifies_and_recaches(self, research, monkeypatch):
        s3 = FakeS3({("radon-archive", f"research/assets/{NAME}"): PDF})
        _client(monkeypatch, s3)
        assert assets.read_asset(NAME) == PDF
        assert s3.requests == [{"Bucket": "radon-archive", "Key": f"research/assets/{NAME}"}]
        assert (research / "assets" / NAME).read_bytes() == PDF
        # Served from the re-cached copy afterwards: no second request.
        assert assets.read_asset(NAME) == PDF and len(s3.requests) == 1

    def test_research_prefix_override_is_honoured(self, research, monkeypatch):
        monkeypatch.setenv("RADON_RESEARCH_ARCHIVE_PREFIX", "r2")
        s3 = FakeS3({("radon-archive", f"r2/assets/{NAME}"): PDF})
        _client(monkeypatch, s3)
        assert assets.read_asset(NAME) == PDF
        assert s3.requests[0]["Key"] == f"r2/assets/{NAME}"

    def test_remote_bytes_with_the_wrong_digest_are_refused_and_not_cached(self, research, monkeypatch):
        s3 = FakeS3({("radon-archive", f"research/assets/{NAME}"): b"%PDF-1.7 tampered"})
        _client(monkeypatch, s3)
        with pytest.raises(ValueError):
            assets.read_asset(NAME)
        assert not (research / "assets" / NAME).exists()

    def test_remote_bytes_of_the_wrong_format_are_refused(self, research, monkeypatch):
        data = b"not a pdf"
        name = hashlib.sha256(data).hexdigest() + ".pdf"
        s3 = FakeS3({("radon-archive", f"research/assets/{name}"): data})
        _client(monkeypatch, s3)
        with pytest.raises(ValueError):
            assets.read_asset(name)

    def test_oversized_remote_object_is_refused_before_reading(self, research, monkeypatch):
        class Huge(FakeS3):
            def get_object(self, **kwargs):
                self.requests.append(kwargs)
                return {"ContentLength": assets.MAX_BYTES["pdf"] + 1, "Body": Body(b"")}

        s3 = Huge()
        _client(monkeypatch, s3)
        with pytest.raises(ValueError):
            assets.read_asset(NAME)

    def test_absent_in_b2_is_a_not_found(self, research, monkeypatch):
        s3 = FakeS3()
        _client(monkeypatch, s3)
        with pytest.raises(FileNotFoundError):
            assets.read_asset(NAME)
        assert s3.requests[0]["Key"] == f"research/assets/{NAME}"

    def test_missing_credentials_never_build_a_client(self, research, monkeypatch):
        monkeypatch.delenv("RADON_ARCHIVE_S3_SECRET_ACCESS_KEY")
        built = _client(monkeypatch, FakeS3())
        with pytest.raises(FileNotFoundError):
            assets.read_asset(NAME)
        assert built == []

    def test_invalid_name_never_reaches_b2(self, research, monkeypatch):
        s3 = FakeS3()
        _client(monkeypatch, s3)
        with pytest.raises(ValueError):
            assets.read_asset("../" + NAME)
        assert s3.requests == []

    def test_read_only_mount_still_serves(self, research, monkeypatch):
        s3 = FakeS3({("radon-archive", f"research/assets/{NAME}"): PDF})
        _client(monkeypatch, s3)

        def read_only(*args, **kwargs):
            raise OSError(30, "Read-only file system")

        monkeypatch.setattr(assets.tempfile, "mkstemp", read_only)
        assert assets.read_asset(NAME) == PDF
        assert not (research / "assets" / NAME).exists()

    def test_slow_transfer_is_bounded_by_the_deadline(self, research, monkeypatch):
        s3 = FakeS3({("radon-archive", f"research/assets/{NAME}"): PDF})
        _client(monkeypatch, s3)
        ticks = iter([0.0] + [assets.READ_THROUGH_DEADLINE_SECS + 1] * 10)
        monkeypatch.setattr(assets.time, "monotonic", lambda: next(ticks))
        monkeypatch.setattr(assets, "READ_THROUGH_CHUNK", 4)
        with pytest.raises(TimeoutError):
            assets.read_asset(NAME)
        assert not (research / "assets" / NAME).exists()

    def test_client_timeouts_fit_inside_the_web_proxy_timeout(self):
        # web/lib/radonApi.ts radonFetchResponse defaults to 30s.
        assert assets.READ_THROUGH_CONNECT_TIMEOUT + assets.READ_THROUGH_DEADLINE_SECS < 30
        assert assets.READ_THROUGH_DEADLINE_SECS <= 20


class TestRoute:
    def test_api_route_serves_a_pruned_asset_from_b2(self, research, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.routes.research import router

        s3 = FakeS3({("radon-archive", f"research/assets/{NAME}"): PDF})
        _client(monkeypatch, s3)
        app = FastAPI()
        app.include_router(router)
        response = TestClient(app).get(f"/newsfeed/research/files/{NAME}")
        assert response.status_code == 200 and response.content == PDF
        assert s3.requests == [{"Bucket": "radon-archive", "Key": f"research/assets/{NAME}"}]

    def test_api_route_404s_when_neither_local_nor_archived(self, research, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.routes.research import router

        _client(monkeypatch, FakeS3())
        app = FastAPI()
        app.include_router(router)
        assert TestClient(app).get(f"/newsfeed/research/files/{NAME}").status_code == 404


def test_store_asset_after_prune_rewrites_the_local_copy(research, monkeypatch):
    s3 = FakeS3()
    _client(monkeypatch, s3)
    source = research / "in.pdf"
    source.write_bytes(PDF)
    assert assets.store_asset(source) == assets.URL_PREFIX + NAME
    assert (research / "assets" / NAME).read_bytes() == PDF and s3.requests == []
    os.unlink(research / "assets" / NAME)
    assert assets.store_asset(source) == assets.URL_PREFIX + NAME
    assert (research / "assets" / NAME).read_bytes() == PDF
