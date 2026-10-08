"""Minimal HTTP GET with a persistent connection per thread and host.

GeoMet is queried hundreds of times per model run; opening a new TLS connection for each request is what
makes it slow (measured: 31 radar images in ~1.5-5 s over kept-alive connections versus 15-29 s without).
"""
import http.client
import threading
import time
import urllib.parse
import urllib.request

_local = threading.local()


def get(url, user_agent, timeout=30, tries=2):
    """Body of a 200 response, or None (network error, 4xx/5xx). Follows redirects through urllib."""
    u = urllib.parse.urlsplit(url)
    path = u.path + ("?" + u.query if u.query else "")
    conns = getattr(_local, "conns", None)
    if conns is None:
        conns = _local.conns = {}
    for attempt in range(tries):
        conn = conns.get(u.netloc)
        if conn is None:
            cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
            conn = conns[u.netloc] = cls(u.netloc, timeout=timeout)
        try:
            conn.request("GET", path, headers={"User-Agent": user_agent})
            resp = conn.getresponse()
            body = resp.read()
            if resp.status == 200:
                return body
            if 300 <= resp.status < 400:                       # rare here: let urllib follow it
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
                    with urllib.request.urlopen(req, timeout=timeout) as r:
                        return r.read()
                except Exception:
                    return None
            if 400 <= resp.status < 500:
                return None
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        conns.pop(u.netloc, None)                              # next attempt reconnects
        if attempt < tries - 1:
            time.sleep(0.3)
    return None
