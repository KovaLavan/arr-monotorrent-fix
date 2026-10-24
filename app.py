import os
from typing import Dict, Tuple, List

import httpx
from fastapi import FastAPI, Request, Response

UPSTREAM = os.getenv("UPSTREAM", "http://192.168.1.235:9696").rstrip("/")
TIMEOUT = float(os.getenv("TIMEOUT", "30"))

app = FastAPI()


class MagnetRedirect(Exception):
    def __init__(self, response: httpx.Response):
        self.response = response


async def _stop_at_magnet(response: httpx.Response):
    if response.has_redirect_location and response.headers["location"].lower().startswith("magnet:"):
        raise MagnetRedirect(response)


class BencodeError(Exception):
    pass


def _parse_int(data: bytes, i: int) -> int:
    # data[i] == b'i'
    j = data.find(b"e", i)
    if j == -1:
        raise BencodeError("unterminated int")
    # allow negative
    try:
        int(data[i + 1 : j])
    except Exception as e:
        raise BencodeError(f"bad int: {e}")
    return j + 1


def _parse_bytes(data: bytes, i: int) -> int:
    # <len>:<payload>
    colon = data.find(b":", i)
    if colon == -1:
        raise BencodeError("missing ':' in bytes")
    try:
        n = int(data[i:colon])
    except Exception as e:
        raise BencodeError(f"bad byte length: {e}")
    start = colon + 1
    end = start + n
    if end > len(data):
        raise BencodeError("bytes overruns buffer")
    return end


def _skip_elem(data: bytes, i: int) -> int:
    if i >= len(data):
        raise BencodeError("unexpected end")
    c = data[i:i+1]
    if c == b"i":
        return _parse_int(data, i)
    if c == b"l":
        i += 1
        while True:
            if i >= len(data):
                raise BencodeError("unterminated list")
            if data[i:i+1] == b"e":
                return i + 1
            i = _skip_elem(data, i)
    if c == b"d":
        i += 1
        while True:
            if i >= len(data):
                raise BencodeError("unterminated dict")
            if data[i:i+1] == b"e":
                return i + 1
            # key must be bytes
            if not (data[i:i+1].isdigit()):
                raise BencodeError("dict key is not bytes")
            i = _skip_elem(data, i)  # skip key bytes
            i = _skip_elem(data, i)  # skip value
    if c.isdigit():
        return _parse_bytes(data, i)
    raise BencodeError(f"unknown token at {i}: {c!r}")


def _read_bytes_key(data: bytes, i: int) -> Tuple[bytes, int, int]:
    # returns (key_bytes, key_start, key_end)
    if i >= len(data) or not data[i:i+1].isdigit():
        raise BencodeError("expected bytes key")
    colon = data.find(b":", i)
    if colon == -1:
        raise BencodeError("missing ':' in key")
    n = int(data[i:colon])
    start = colon + 1
    end = start + n
    if end > len(data):
        raise BencodeError("key overruns buffer")
    return data[start:end], i, end


def _bencode_bytes(s: bytes) -> bytes:
    return str(len(s)).encode("ascii") + b":" + s


def fix_top_level_dict_key_order(torrent: bytes) -> bytes:
    # Only reorder top-level dict keys; preserve raw value bytes for each key.
    if not torrent.startswith(b"d"):
        return torrent  # not bencode dict, don't touch

    i = 1
    items: List[Tuple[bytes, bytes]] = []

    while True:
        if i >= len(torrent):
            raise BencodeError("unterminated top dict")
        if torrent[i:i+1] == b"e":
            end = i + 1
            break

        key, key_start, key_end = _read_bytes_key(torrent, i)
        i = key_end

        val_start = i
        i = _skip_elem(torrent, i)
        val_end = i

        # store raw value bytes exactly as-is
        raw_val = torrent[val_start:val_end]
        items.append((key, raw_val))

    # If it's already ordered, do nothing
    keys = [k for k, _ in items]
    if keys == sorted(keys):
        return torrent[:end]  # original up to 'e' (should be whole file anyway)

    # Rebuild: d + sorted keys + raw values + e
    out = bytearray()
    out += b"d"
    for k, raw_val in sorted(items, key=lambda kv: kv[0]):
        out += _bencode_bytes(k)
        out += raw_val
    out += b"e"

    # If there were trailing bytes (shouldn't happen), keep them
    if end < len(torrent):
        out += torrent[end:]
    return bytes(out)


def _should_fix(content_type: str | None, body: bytes) -> bool:
    if content_type and "bittorrent" in content_type.lower():
        return True
    # most torrents start with 'd'
    return body.startswith(b"d")


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def proxy(path: str, request: Request):
    # Build upstream URL
    url = f"{UPSTREAM}/{path}"
    if request.url.query:
        url += f"?{request.url.query}"

    # Forward headers (drop hop-by-hop)
    hop_by_hop = {
        "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "te", "trailers", "transfer-encoding", "upgrade"
    }
    headers = {k: v for k, v in request.headers.items() if k.lower() not in hop_by_hop}

    body = await request.body()

    async with httpx.AsyncClient(
        timeout=TIMEOUT, follow_redirects=True,
        event_hooks={"response": [_stop_at_magnet]},
    ) as client:
        try:
            r = await client.request(
                request.method,
                url,
                headers=headers,
                content=body if body else None,
            )
        except MagnetRedirect as redirect:
            # Arr handles magnet redirects as 301/302/303, not 307/308.
            status = redirect.response.status_code
            if status in (307, 308):
                status = 302
            return Response(status_code=status, headers={"location": redirect.response.headers["location"]})

    resp_headers = {k: v for k, v in r.headers.items() if k.lower() not in hop_by_hop}
    content = r.content

    # Fix only the /download endpoint (or any bittorrent payload)
    if path.lower().endswith("download") or "download" in path.lower():
        if _should_fix(r.headers.get("content-type"), content):
            try:
                content = fix_top_level_dict_key_order(content)
            except Exception:
                # If anything goes wrong, return original content (better than breaking downloads)
                content = r.content

    return Response(content=content, status_code=r.status_code, headers=resp_headers)
