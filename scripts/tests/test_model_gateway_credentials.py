"""The gateway reads only fixed model slots; API consumers do not retain model keys."""
from __future__ import annotations
import os
import pytest

# The interrupt tests assert that a wait ends early, not that it ends by a
# fixed clock. Holder and bound scale together, so a loaded host cannot invert
# the comparison while a broken interrupt still blows past the bound.
HOLD_SECONDS = 5.0
INTERRUPT_BOUND_SECONDS = 1.0


def test_vault_broker_exposes_only_fixed_model_key_slots():
    import subscription_vault
    class Vault:
        def model_key(self, provider):
            assert provider in {"nvidia", "cerebras"}
            return "model-secret"
    assert subscription_vault.handle(Vault(), {"op": "model-key", "provider": "nvidia"}) == {"ok": True, "value": "model-secret"}
    assert subscription_vault.handle(Vault(), {"op": "model-key", "provider": "IB_PASSWORD"})["ok"] is False


def test_gateway_reads_current_keys_from_broker_without_returning_them(monkeypatch):
    from model_gateway import credentials
    class Broker:
        def model_key(self, provider):
            return provider + "-new"
    monkeypatch.setenv("RADON_SUBSCRIPTION_VAULT_SOCKET", "/private/socket")
    monkeypatch.setattr(credentials, "connect_vault", lambda *a, **k: Broker())
    monkeypatch.setenv("NVIDIA_API_KEY", "old")
    credentials.refresh_keys()
    assert os.environ["NVIDIA_API_KEY"] == "nvidia-new"
    assert os.environ["CEREBRAS_API_KEY"] == "cerebras-new"


def test_gateway_does_not_replace_store_failure_with_stale_environment(monkeypatch):
    from model_gateway import credentials
    from model_gateway.client import GatewayError
    monkeypatch.setenv("RADON_SUBSCRIPTION_VAULT_SOCKET", "/private/socket")
    monkeypatch.setenv("NVIDIA_API_KEY", "old")
    monkeypatch.setattr(credentials, "connect_vault", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("private diagnostic")))
    with pytest.raises(GatewayError) as error:
        credentials.refresh_keys()
    assert error.value.code == "credential_store_unavailable"
    assert "private diagnostic" not in str(error.value)


def test_api_bootstrap_keeps_model_secrets_out_of_consumer_environment(monkeypatch):
    from api.routes import credentials
    class Store:
        def get_secret(self, name):
            return "stored-" + name
    monkeypatch.setattr(credentials, "_store", lambda: Store())
    monkeypatch.setattr(credentials, "_stored_by_name", lambda store: {"NVIDIA_API_KEY": {}, "CEREBRAS_API_KEY": {}, "UW_TOKEN": {}})
    monkeypatch.setattr(credentials.credentials_registry, "all_field_names", lambda: ["NVIDIA_API_KEY", "CEREBRAS_API_KEY", "UW_TOKEN"])
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    result = credentials.bootstrap_exported_names()
    assert "NVIDIA_API_KEY" not in result and "CEREBRAS_API_KEY" not in result
    assert "NVIDIA_API_KEY" not in os.environ and "CEREBRAS_API_KEY" not in os.environ
    assert os.environ["UW_TOKEN"] == "stored-UW_TOKEN"


def test_api_store_outage_still_scrubs_inherited_model_secrets(monkeypatch):
    from api.routes import credentials
    monkeypatch.setenv('NVIDIA_API_KEY','inherited-secret')
    monkeypatch.setenv('CEREBRAS_API_KEY','inherited-secret')
    monkeypatch.setattr(credentials,'_store',lambda:(_ for _ in ()).throw(RuntimeError('offline')))
    credentials.bootstrap_exported_names()
    assert 'NVIDIA_API_KEY' not in os.environ and 'CEREBRAS_API_KEY' not in os.environ


def test_deleted_store_overlay_cannot_remain_in_gateway_env(monkeypatch):
    from model_gateway import credentials
    state={'value':'stored-key'}
    class Broker:
        def model_key(self,provider): return state['value'] if provider=='nvidia' else None
    monkeypatch.setenv('RADON_SUBSCRIPTION_VAULT_SOCKET','/private/socket')
    monkeypatch.delenv('NVIDIA_API_KEY',raising=False)
    monkeypatch.setattr(credentials,'connect_vault',lambda *a,**k:Broker())
    monkeypatch.setattr(credentials,'_FALLBACK',{},raising=False)
    credentials.refresh_keys();assert os.environ['NVIDIA_API_KEY']=='stored-key'
    state['value']=None;credentials.refresh_keys()
    assert 'NVIDIA_API_KEY' not in os.environ


def test_deleted_overlay_returns_only_explicit_launch_fallback(monkeypatch):
    from model_gateway import credentials
    state={'value':'stored-key'}
    class Broker:
        def model_key(self,provider): return state['value'] if provider=='nvidia' else None
    monkeypatch.setenv('RADON_SUBSCRIPTION_VAULT_SOCKET','/private/socket')
    monkeypatch.setenv('NVIDIA_API_KEY','configured-launch-key')
    monkeypatch.setattr(credentials,'connect_vault',lambda *a,**k:Broker())
    monkeypatch.setattr(credentials,'_FALLBACK',{},raising=False)
    credentials.refresh_keys();assert os.environ['NVIDIA_API_KEY']=='stored-key'
    state['value']=None;credentials.refresh_keys()
    assert os.environ['NVIDIA_API_KEY']=='configured-launch-key'


def test_credential_lock_wait_respects_shared_deadline(monkeypatch):
    import threading
    import time
    from model_gateway import credentials, engine
    from model_gateway.contracts import ModelResponseError
    monkeypatch.setenv('RADON_SUBSCRIPTION_VAULT_SOCKET','/private/socket')
    monkeypatch.setattr(credentials,'connect_vault',lambda *a,**k:pytest.fail('expired request reached broker'))
    lock=threading.Lock();lock.acquire();monkeypatch.setattr(credentials,'_LOCK',lock)
    release=threading.Timer(HOLD_SECONDS,lock.release);release.start()
    started=time.monotonic()
    try:
        with engine.attempt_scope(deadline=started+.05,seen=set()):
            with pytest.raises(ModelResponseError) as error:credentials.refresh_keys()
        assert error.value.code=='deadline_exceeded'
        assert time.monotonic()-started < INTERRUPT_BOUND_SECONDS
    finally:release.join()


def test_credential_lock_wait_observes_cancellation(monkeypatch):
    import threading
    import time
    from model_gateway import credentials, engine
    from model_gateway.contracts import ModelResponseError
    monkeypatch.setenv('RADON_SUBSCRIPTION_VAULT_SOCKET','/private/socket')
    monkeypatch.setattr(credentials,'connect_vault',lambda *a,**k:pytest.fail('cancelled request reached broker'))
    lock=threading.Lock();lock.acquire();monkeypatch.setattr(credentials,'_LOCK',lock)
    cancel=threading.Event();abort=threading.Timer(.04,cancel.set);abort.start()
    release=threading.Timer(HOLD_SECONDS,lock.release);release.start()
    started=time.monotonic()
    try:
        with engine.attempt_scope(deadline=started+1,seen=set(),cancelled=cancel.is_set):
            with pytest.raises(ModelResponseError) as error:credentials.refresh_keys()
        assert error.value.code=='cancelled'
        assert time.monotonic()-started < INTERRUPT_BOUND_SECONDS
    finally:abort.join();release.join()


@pytest.mark.parametrize('cancelled',[False,True])
def test_real_stalled_vault_stops_at_request_deadline_or_disconnect(monkeypatch,cancelled):
    import socket
    import tempfile
    import threading
    import time
    from pathlib import Path
    from model_gateway import credentials, engine
    from model_gateway.contracts import ModelResponseError
    import subscription_vault
    folder=Path(tempfile.mkdtemp(prefix='mgv-',dir='/tmp'));path=folder/'s'
    listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);listener.bind(str(path));listener.listen(1)
    entered=threading.Event();stop=threading.Event();abort=threading.Event()
    def stalled():
        with listener.accept()[0] as connection:
            connection.recv(4096);entered.set();stop.wait(HOLD_SECONDS)
    worker=threading.Thread(target=stalled,daemon=True);worker.start()
    monkeypatch.setenv('RADON_SUBSCRIPTION_VAULT_SOCKET',str(path))
    monkeypatch.setattr(subscription_vault,'IO_TIMEOUT_SECONDS',HOLD_SECONDS)
    monkeypatch.setenv('NVIDIA_API_KEY','old-key')
    timer=threading.Timer(.04,abort.set) if cancelled else None
    if timer:timer.start()
    started=time.monotonic()
    try:
        with engine.attempt_scope(deadline=started+(1 if cancelled else .06),seen=set(),cancelled=abort.is_set):
            with pytest.raises(ModelResponseError) as error:credentials.refresh_keys()
        assert entered.is_set()
        assert error.value.code==('cancelled' if cancelled else 'deadline_exceeded')
        assert time.monotonic()-started < INTERRUPT_BOUND_SECONDS
        assert os.environ['NVIDIA_API_KEY']=='old-key'  # Failed lookup never publishes partial/stale success.
    finally:
        stop.set();worker.join(HOLD_SECONDS);listener.close();path.unlink();folder.rmdir()
        if timer:timer.join()
