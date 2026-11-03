"""Consumer cancellation closes private transport and cannot reconnect after abort."""
import threading
import time
import pytest
from model_gateway import client


def test_cancelled_connect_never_opens_another_socket(monkeypatch):
    conn=client.UnixHTTPConnection('/never/open',1)
    conn.cancelled=lambda:True
    monkeypatch.setattr(client.socket,'socket',lambda *a,**k:pytest.fail('cancelled connection reopened'))
    with pytest.raises(client.GatewayError) as error: conn.connect()
    assert error.value.code=='cancelled'


def test_cancel_during_connect_closes_socket_and_returns_coded_error(monkeypatch):
    aborted=threading.Event();closed=[]
    class Socket:
        def settimeout(self,*a): pass
        def connect(self,*a): aborted.set()
        def close(self): closed.append(True)
    monkeypatch.setattr(client.socket,'socket',lambda *a,**k:Socket())
    conn=client.UnixHTTPConnection('/private/socket',1);conn.cancelled=aborted.is_set
    with pytest.raises(client.GatewayError) as error: conn.connect()
    assert error.value.code=='cancelled' and closed==[True]


def test_real_unix_http_cancel_unblocks_inflight_response(monkeypatch):
    import tempfile
    from pathlib import Path
    from model_gateway.server import GatewayServer
    entered=threading.Event();cancel=threading.Event();saw_disconnect=threading.Event()
    class Gateway:
        def handle(self,operation,payload,*,cancelled):
            entered.set();end=time.monotonic()+2
            while time.monotonic()<end:
                if cancelled():saw_disconnect.set();break
                time.sleep(.005)
            return {'status':'ok'}
    folder=Path(tempfile.mkdtemp(prefix='mg-',dir='/tmp'));folder.chmod(0o700)
    server=GatewayServer(str(folder/'s'),Gateway());worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    monkeypatch.setenv(client.SOCKET_ENV,str(folder/'s'))
    result=[]
    def send():
        try:client.request('json',{'instruction':'x'},cancelled=cancel.is_set)
        except client.GatewayError as error:result.append(error.code)
    waiter=threading.Thread(target=send,daemon=True);waiter.start()
    try:
        assert entered.wait(1);cancel.set();waiter.join(1)
        assert result==['cancelled'] and not waiter.is_alive()
        assert saw_disconnect.wait(1)
    finally:
        server.shutdown();server.server_close();folder.rmdir()
