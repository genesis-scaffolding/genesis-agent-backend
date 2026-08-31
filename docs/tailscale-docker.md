# Tailscale → Docker connectivity blocked by ufw-docker

A known issue when running Docker containers on Omarchy hosts behind
Tailscale. The fix is a one-liner script: `scripts/tailscale-docker-fix.sh`.

## Symptoms

You expose a service as a Docker container (`-p 9090:8000` etc.). It works:

- ✅ From the host itself (`127.0.0.1:9090`)
- ✅ From the LAN (`192.168.x.x:9090`)
- ✅ From the host via its own Tailscale IP (`100.x.x.x:9090`)

But it **fails** from another Tailscale peer (phone, second machine):

- ❌ `p3:9090` from your phone → connection times out, no log line in the container
- ❌ `curl http://100.x.x.x:9090/` from another Tailscale host → connection times out

The container's log shows nothing because the packet is dropped by the kernel
**before** it reaches the container.

## Root cause

Omarchy's first-run setup runs `sudo ufw-docker install` (see
`~/.local/share/omarchy/install/first-run/firewall.sh`). ufw-docker adds
a chain called `DOCKER-USER` with rules that block traffic from
"non-private" sources to RFC 1918 private ranges:

```
-A DOCKER-USER -j RETURN -s 10.0.0.0/8
-A DOCKER-USER -j RETURN -s 172.16.0.0/12
-A DOCKER-USER -j RETURN -s 192.168.0.0/16
-A DOCKER-USER -j ufw-docker-logging-deny -m conntrack --ctstate NEW -d 10.0.0.0/8
-A DOCKER-USER -j ufw-docker-logging-deny -m conntrack --ctstate NEW -d 172.16.0.0/12
-A DOCKER-USER -j ufw-docker-logging-deny -m conntrack --ctstate NEW -d 192.168.0.0/16
```

The `logging-deny` chain ends in a `DROP`. Tailscale's CGNAT range
(`100.64.0.0/10`) is **not** in the source whitelist, so Tailscale traffic
to the Docker bridge (`172.17.0.x`) hits the deny rule and is silently
dropped.

### Why the host itself still works

When you connect *from the host* (e.g. `curl http://100.125.22.29:9090/`
on the same machine), the kernel delivers via the **INPUT chain** locally.
FORWARD isn't involved because source and destination are both local.
Tailscale packets from a peer, however, are non-local → they traverse
**FORWARD** → hit DOCKER-USER → dropped.

### Why some Omarchy machines work by accident

Older versions of ufw-docker generated DOCKER-USER rules with narrower
matching (TCP SYN-only, not `ctstate NEW`). The package on your system is
the same version (`251123-1`), but the *generated* rules differ depending
on when `/etc/ufw/after.rules` was last regenerated. A machine that hasn't
been re-set up since the broader rules shipped (around mid-2025) may have
narrower rules in `after.rules` that don't catch Tailscale traffic.

This is fragile: the moment anyone runs `sudo ufw-docker install` or
`sudo ufw reload` after the rule-generation logic updates, the machine
will start dropping Tailscale traffic.

## Verify the issue

Without sudo:

```bash
# Run on the same machine you can't reach via Tailscale
docker run -d --name test-nginx -p 9096:80 nginx:alpine
# From a Tailscale peer (another machine or phone):
curl http://<this-host-tailscale-ip>:9096/
```

If the connection times out, the issue is ufw-docker.

With sudo, you can pinpoint the kill shot:

```bash
sudo iptables -L DOCKER-USER -n -v
# Look for a rule matching destination 172.16.0.0/12 with non-zero
# packet count — that's the rule eating your Tailscale SYN packets.
sudo iptables -L ufw-docker-logging-deny -n -v
# This chain ends in DROP. If packets reached it, they're gone.
```

A non-zero packet count on the `logging-deny` rule for `172.16.0.0/12` is
the smoking gun.

## Fix

Run the script (requires sudo):

```bash
sudo ./scripts/tailscale-docker-fix.sh install
```

It adds a RETURN rule at position 1 of DOCKER-USER for Tailscale's CGNAT
range (`100.64.0.0/10 → 172.16.0.0/12`), so it bypasses ufw-docker's
logging-deny chain. The rule is also persisted to
`/etc/ufw/after.rules` so it survives reboots and UFW reloads.

Verify with:

```bash
sudo ./scripts/tailscale-docker-fix.sh check
```

Remove with:

```bash
sudo ./scripts/tailscale-docker-fix.sh rollback
```

## Optional: a deeper fix

The real root cause is that ufw-docker's `cidr_list` is hardcoded to
RFC 1918:

```bash
# /usr/bin/ufw-docker, ~line 418
cidr_list=(10.0.0.0/8 172.16.0.0/12 192.168.0.0/16)
```

Editing this to add `100.64.0.0/10` (and similar ranges for ZeroTier,
Nebula, etc.) and re-running `ufw-docker install` makes the fix survive
future ufw-docker updates. Our one-line script is a less invasive
alternative that doesn't touch the system package.

## Related

- The two `/etc/ufw/after.rules` files on your machines may differ
  depending on when ufw-docker last regenerated them. Comparing them
  reveals the stale-vs-current state.
- See `~/.local/share/omarchy/install/first-run/firewall.sh` for the
  Omarchy installer line that triggers this (`sudo ufw-docker install`).