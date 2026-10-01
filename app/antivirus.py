"""Scan uploads before decoding or retaining them. Clamd is private infrastructure."""
import os
import socket
import struct

from fastapi import HTTPException


def scan_upload(blob: bytes) -> None:
    if os.getenv("ANTIVIRUS_REQUIRED", "false").lower() != "true":
        return
    try:
        with socket.create_connection((os.getenv("CLAMD_HOST", "clamav"),
                                       int(os.getenv("CLAMD_PORT", "3310"))), timeout=30) as connection:
            connection.sendall(b"zINSTREAM\0")
            for offset in range(0, len(blob), 65536):
                chunk = blob[offset:offset + 65536]
                connection.sendall(struct.pack("!I", len(chunk)) + chunk)
            connection.sendall(struct.pack("!I", 0))
            response = bytearray()
            while b"\0" not in response and len(response) < 4096:
                chunk = connection.recv(512)
                if not chunk:
                    break
                response.extend(chunk)
        if bytes(response) == b"stream: OK\0":
            return
        if bytes(response).endswith(b" FOUND\0"):
            raise HTTPException(422, "This file was rejected by the malware scanner.")
    except (OSError, ValueError):
        pass
    raise HTTPException(503, "File safety check is unavailable. Please retry the upload later.")
