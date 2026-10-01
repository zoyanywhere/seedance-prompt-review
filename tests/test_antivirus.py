import socket
import struct
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException

from app.antivirus import scan_upload


@pytest.mark.parametrize("reply,expected", [
    (b"stream: OK\0", None),
    (b"stream: Test.Signature FOUND\0", 422),
    (b"stream: size limit exceeded ERROR\0", 503),
    (b"stream: OK", 503),
    (b"nonsense\0", 503),
])
def test_stream_protocol_and_fail_closed(monkeypatch, reply, expected):
    payload = b"reference bytes" * 10000
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server.settimeout(5)
        monkeypatch.setenv("ANTIVIRUS_REQUIRED", "true")
        monkeypatch.setenv("CLAMD_HOST", "127.0.0.1")
        monkeypatch.setenv("CLAMD_PORT", str(server.getsockname()[1]))

        def receive_exact(connection, size):
            result = bytearray()
            while len(result) < size:
                part = connection.recv(size - len(result))
                assert part
                result.extend(part)
            return bytes(result)

        def daemon():
            with server.accept()[0] as connection:
                connection.settimeout(5)
                assert receive_exact(connection, 10) == b"zINSTREAM\0"
                result = bytearray()
                while True:
                    size = struct.unpack("!I", receive_exact(connection, 4))[0]
                    if size == 0:
                        break
                    result.extend(receive_exact(connection, size))
                assert bytes(result) == payload
                connection.sendall(reply)

        with ThreadPoolExecutor(max_workers=1) as pool:
            worker = pool.submit(daemon)
            if expected:
                with pytest.raises(HTTPException) as error:
                    scan_upload(payload)
                assert error.value.status_code == expected
            else:
                scan_upload(payload)
            worker.result(timeout=5)


def test_unreachable_scanner_rejects(monkeypatch):
    monkeypatch.setenv("ANTIVIRUS_REQUIRED", "true")
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    with pytest.raises(HTTPException) as error:
        scan_upload(b"reference")
    assert error.value.status_code == 503


def test_local_scanner_is_opt_in(monkeypatch):
    monkeypatch.delenv("ANTIVIRUS_REQUIRED", raising=False)
    scan_upload(b"local reference")
