"""A tiny stdlib HTTP layer shared by the bank and broker adapters. Tests swap the transport."""
from __future__ import annotations

import socket
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    headers: dict


class NetworkError(Exception):
    """The request never got an HTTP answer (DNS, TLS, timeout, reset)."""


# A transport sends one HTTP request and returns a Response or raises NetworkError.
Transport = Callable[[str, str, dict, "bytes | None"], Response]


def make_transport(context: ssl.SSLContext | None = None, timeout: float = TIMEOUT_SECONDS) -> Transport:
    def transport(method: str, url: str, headers: dict, body: bytes | None) -> Response:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=context) as r:
                return Response(r.status, r.read(), dict(r.headers))
        except urllib.error.HTTPError as e:
            return Response(e.code, e.read() or b"", dict(e.headers or {}))
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, ssl.SSLError) as e:
            raise NetworkError(type(e).__name__) from None
    return transport


def header(resp: Response, name: str) -> str | None:
    return {k.lower(): v for k, v in resp.headers.items()}.get(name.lower())
