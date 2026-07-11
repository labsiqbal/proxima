# Proxima over Tailscale

Proxima should be exposed to phones/laptops over HTTPS for PWA installability.

## Simple tailnet-only serve

After `proxima serve` is listening on `127.0.0.1:8765`:

```bash
tailscale serve --bg https / http://127.0.0.1:8765
```

Then open the HTTPS MagicDNS URL on a device joined to the same tailnet and install the PWA from the browser.

## Notes

- Tailscale is the access boundary. Proxima is single-user and treats anyone who
  reaches the API as the owner.
- Keep Proxima bound to `127.0.0.1` unless you intentionally place it behind a reverse proxy.
- Do not expose raw Hermes ports to the browser; the PWA should talk only to Proxima.
