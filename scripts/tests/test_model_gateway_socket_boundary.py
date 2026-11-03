"""UDS management separation and bounded connection admission."""
import os
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
from model_gateway.server import GatewayServer, Handler


def test_live_service_socket_cannot_be_replaced():
    import tempfile
    parent=Path(tempfile.mkdtemp(prefix="mg-",dir="/tmp")); parent.chmod(0o700)
    path=str(parent/'gateway.sock')
    first=GatewayServer(path,SimpleNamespace())
    try:
        with pytest.raises(OSError,match='active'):
            GatewayServer(path,SimpleNamespace())
        assert Path(path).is_socket()
    finally:
        first.server_close()
        parent.rmdir()


def test_control_operations_are_not_on_inference_socket():
    server=GatewayServer.__new__(GatewayServer)
    server.management=False;server.allowed_uid=os.getuid();server.allowed_uids={os.getuid(),1200}
    assert server.permits('json',os.getuid())
    assert server.permits('native',1200)
    for op in ['refresh','refresh-status','refresh-catalog','validate-credential']:
        assert not server.permits(op,os.getuid())
        assert not server.permits(op,1200)
    server.management=True
    assert server.permits('refresh',os.getuid())
    assert not server.permits('refresh',1200)


def test_untrusted_uid_never_receives_any_operation():
    server=GatewayServer.__new__(GatewayServer)
    server.management=False;server.allowed_uid=os.getuid();server.allowed_uids={os.getuid()}
    assert not server.permits('json',999999)


def test_bounded_handler_admission():
    assert GatewayServer.MAX_CONNECTIONS == 24
    server=GatewayServer.__new__(GatewayServer)
    assert hasattr(server,'process_request')
    import inspect
    assert '_connections.acquire(blocking=False)' in inspect.getsource(GatewayServer.process_request)


def test_client_uses_separate_control_parent_for_management(monkeypatch):
    from model_gateway import client
    seen=[]
    class Reply:
        status=200
        def read(self,*a): return b'{}'
    class Conn:
        def __init__(self,path,timeout): seen.append(path)
        def request(self,*a,**k): pass
        def getresponse(self): return Reply()
        def close(self): pass
    monkeypatch.setattr(client,'UnixHTTPConnection',Conn)
    monkeypatch.setenv('RADON_MODEL_GATEWAY_SOCKET','/private/inference/gateway.sock')
    monkeypatch.setenv('RADON_MODEL_CONTROL_SOCKET','/private/control/control.sock')
    client.request('models')
    client.request('validate-credential',{'provider':'nvidia','candidate':'fake'})
    assert seen==['/private/inference/gateway.sock','/private/control/control.sock']


def test_live_healthy_peer_is_not_mistaken_for_disconnected():
    import time
    left,right=socket.socketpair()
    left.settimeout(.03)
    handler=Handler.__new__(Handler);handler.connection=left
    try:
        started=time.monotonic()
        assert handler._cancelled() is False
        assert time.monotonic()-started < .02
    finally:left.close();right.close()


def test_idle_headers_release_handler_capacity(monkeypatch):
    import tempfile
    import time
    from model_gateway import client
    parent=Path(tempfile.mkdtemp(prefix='mg-',dir='/tmp'));parent.chmod(0o700)
    path=str(parent/'s')
    monkeypatch.setattr(Handler,'HEADER_TIMEOUT',.03,raising=False)
    gateway=SimpleNamespace(handle=lambda *a,**k:{'status':'ok'})
    server=GatewayServer(path,gateway)
    import threading
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    idle=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);idle.settimeout(.3);idle.connect(path)
    try:
        idle.sendall(b'POST /v1/json HTTP/1.1\r\n')
        assert idle.recv(1)==b''
        monkeypatch.setenv(client.SOCKET_ENV,path)
        assert client.request('status',timeout_seconds=1)=={'status':'ok'}
    finally:
        idle.close();server.shutdown();server.server_close();parent.rmdir()


def test_additional_sandbox_uid_has_inference_group_access_only():
    import tempfile
    parent=Path(tempfile.mkdtemp(prefix='mg-',dir='/tmp'));parent.chmod(0o700)
    server=None
    try:
        server=GatewayServer(str(parent/'s'),SimpleNamespace(),client_uids=(996,),tunnel_gid=os.getgid())
        assert server.permits('native',996)
        assert not server.permits('refresh',996)
        assert parent.stat().st_mode & 0o777==0o710
        assert (parent/'s').stat().st_mode & 0o777==0o660
    finally:
        if server is not None:server.server_close()
        parent.rmdir()
