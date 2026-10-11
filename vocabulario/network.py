import socket
import sys

import httpx
from telegram.request import HTTPXRequest


def telegram_request(*, polling=False):
    """Retry connection failures, never replay an already submitted Bot API request."""
    size = 1 if polling else 8
    # Smaller segments avoid fragile paths to Telegram on some VPS networks.
    options = [(socket.IPPROTO_TCP, socket.TCP_MAXSEG, 1200)] if sys.platform == "linux" else None
    transport = httpx.AsyncHTTPTransport(
        retries=4,
        socket_options=options,
        limits=httpx.Limits(max_connections=size, max_keepalive_connections=size, keepalive_expiry=120),
    )
    return HTTPXRequest(
        connection_pool_size=size,
        connect_timeout=4,
        read_timeout=30,
        write_timeout=30,
        pool_timeout=5,
        media_write_timeout=60,
        httpx_kwargs={"transport": transport},
    )


def exception_names(error):
    """Log failure phases without URLs, bot tokens, or message contents."""
    names, seen = [], set()
    while error is not None and id(error) not in seen and len(names) < 6:
        names.append(type(error).__name__)
        seen.add(id(error))
        error = error.__cause__
    return " <- ".join(names)
