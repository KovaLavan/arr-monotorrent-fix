# arr-monotorrent-fix

Fixes **Radarr / Sonarr** torrent grabs failing with **MonoTorrent** errors when a tracker serves a valid `.torrent` file that qBittorrent accepts but MonoTorrent rejects.

Most commonly caused by **non-canonical bencode** (unordered dictionary keys).

## Fixes

- `Invalid torrent file specified`
- `Illegal BEncodedDictionary`
- Torrents that work when added manually to qBittorrent but fail in Radarr/Sonarr

## Safe for private trackers

The proxy does **not** modify the raw `info` dictionary, so the **infohash is preserved**.

## Usage

Run the proxy in front of your indexer service:

```bash
docker run -d --name arr-monotorrent-fix \
  -e UPSTREAM="http://PROWLARR_IP:9696" \
  -p 8788:8787 \
  --restart unless-stopped \
  kovalavan/arr-monotorrent-fix:stable
```

## Radarr / Sonarr configuration

Edit the affected indexer(s):

Change base URL from:

`http://PROWLARR_IP:9696`

to:

`http://PROWLARR_IP:8788`

Everything else stays the same.

## Upstream compatibility:
Works with Prowlarr, Jackett, or any Torznab-compatible indexer service.
Simply point Radarr / Sonarr to the proxy instead of the upstream service.
