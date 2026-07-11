Proxima has no login wall of its own — by design, whoever can reach this page is treated as the owner. The network layer is the lock. Keep the app bound to `127.0.0.1` and reach it through one of the two private paths below. Never bind it to a public interface and never put it behind an unauthenticated tunnel.

All commands below assume the default port `8765` — adjust if you changed `PROXIMA_PORT`.

## Option 1 — Tailscale (recommended: free, private)

Best for using Proxima from your own phone or laptop anywhere. Nothing is exposed to the public internet — only devices signed into your tailnet can connect, and you get a stable HTTPS URL for free.

1. Install Tailscale on this server and sign in ([tailscale.com/download](https://tailscale.com/download)):

   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   ```

2. Let your user manage Tailscale, then serve Proxima:

   ```bash
   sudo tailscale set --operator=$(whoami)
   tailscale serve --bg 8765
   ```

3. Read your URL back — it is stable across reboots:

   ```bash
   tailscale serve status
   # https://<machine>.<your-tailnet>.ts.net → http://127.0.0.1:8765
   ```

   If certificates complain, enable **MagicDNS** and **HTTPS Certificates** on the DNS page of the [Tailscale admin console](https://login.tailscale.com/admin/dns).

4. Install the Tailscale app on your phone/laptop, sign into the same tailnet, and open that URL.

To stop serving: `tailscale serve reset`

## Option 2 — Cloudflare Tunnel + your own domain (public URL, email-gated)

Best for a stable `https://proxima.yourdomain.com` that opens in any browser with no client app. Cloudflare Access (free for up to 50 users) forces an email check before any request reaches Proxima.

You need a domain you own, added to Cloudflare — the free plan is fine. Cloudflare does not hand out free permanent domains; if you don't own one, use Option 1 instead.

1. **Protect the hostname first**, so it is never open for even a minute. In the Cloudflare dashboard → **Zero Trust → Access → Applications → Add an application → Self-hosted**:
   - Application domain: `proxima.yourdomain.com`
   - Policy: **Allow** → Include → **Emails** → your email address

2. Install [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) on this server and sign in:

   ```bash
   cloudflared tunnel login
   ```

3. Create the tunnel and point your hostname at it:

   ```bash
   cloudflared tunnel create proxima
   cloudflared tunnel route dns proxima proxima.yourdomain.com
   ```

4. Tell the tunnel where Proxima runs — create `~/.cloudflared/config.yml` (fill in your username and the tunnel ID printed by the create command):

   ```yaml
   tunnel: proxima
   credentials-file: /home/YOU/.cloudflared/TUNNEL_ID.json
   ingress:
     - hostname: proxima.yourdomain.com
       service: http://127.0.0.1:8765
     - service: http_status:404
   ```

5. Test it, then install it as a service so it survives reboots:

   ```bash
   cloudflared tunnel run proxima   # test run — Ctrl-C to stop
   sudo cloudflared service install     # install as a system service
   ```

6. Open `https://proxima.yourdomain.com`: Cloudflare Access asks for a one-time code sent to your email, then Proxima loads.

To stop: `sudo systemctl disable --now cloudflared`

## Why is there no free public-tunnel option?

Free quick tunnels (`https://…trycloudflare.com`) are unauthenticated: anyone who has the URL gets full owner access — terminal, files, agents — because Proxima deliberately has no second login wall behind the network gate. The free-and-safe option is Tailscale above.
