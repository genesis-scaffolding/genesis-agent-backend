# Docker container → host services blocked by UFW

A known issue when Docker containers need to reach services running on the host
machine (e.g., llama-swap running on bare metal while bifrost runs in a
container). The fix requires both `ufw-docker` and a raw UFW INPUT rule.

## Symptoms

A container on the host can reach external services but **not host-bound
services**:

- ✅ `ping 8.8.8.8` from inside container works
- ✅ `ping 172.17.0.1` (docker0 gateway) works (ICMP is allowed)
- ✅ `curl https://api.example.com` from container works
- ❌ `wget http://172.17.0.1:8080` from container → **timeout**
- ❌ `wget http://192.168.8.x:8080` from container → **timeout**
- ❌ `wget http://100.125.22.29:8080` from container → **timeout**

Llama-swap logs show no incoming request (packet dropped before reaching the
service).

## Root cause

UFW's INPUT chain has a default-drop policy. The traffic flow is:

```
Container (172.17.0.4)
    ↓
docker0 bridge (172.17.0.1)
    ↓
Host's eth0 / tailscale0
    ↓
INPUT chain (ufw default DROP policy)
    ↓
DROP ← No rule matches container source IPs!
```

UFW's INPUT chain has rules for:
- Loopback traffic (`iif lo accept`)
- Established/related connections
- ICMP (ping)
- SSH

But **no rule** accepts traffic from Docker's bridge network (`172.17.0.0/16`).

**Why DOCKER-USER doesn't help here:** The `DOCKER-USER` chain handles
**FORWARD** traffic (container-to-container, container-to-external). Traffic
destined for the host itself goes through **INPUT**, not FORWARD. ufw-docker
only manages DOCKER-USER.

### Why ping works but TCP fails

UFW explicitly allows ICMP:
```
-A ufw-user-input -p icmp --icmp-type destination-unreachable -j ACCEPT
-A ufw-user-input -p icmp --icmp-type echo-request -j ACCEPT
```

TCP has no such rule. SYN packets hit the DROP policy.

## Verify the issue

```bash
# From inside any container
docker exec bifrost wget -q -O - --timeout=5 http://172.17.0.1:8080/v1/models
# Expected: timeout

# From the host (this works)
curl -s http://127.0.0.1:8080/v1/models
# Expected: JSON response

# Check UFW INPUT rules (no rule for 172.17.0.0/16)
sudo ufw status numbered | grep 172.17
# Expected: empty
```

## The fix

Two parts are required:

### 1. Install ufw-docker (for FORWARD/DOCKER-USER chain)

This handles traffic from Tailscale peers to containers (the other issue).

```bash
sudo ufw-docker install --docker-subnets 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10
```

The `--docker-subnets` flag includes Tailscale's CGNAT range so you don't
need a separate fix for that.

### 2. Add UFW INPUT rule for Docker bridge

```bash
# Allow containers to reach all host services
sudo ufw allow from 172.17.0.0/16

# Or restrict to specific ports
sudo ufw allow from 172.17.0.0/16 to any port 8080,8000,3000
```

Reload UFW:
```bash
sudo ufw reload
```

### Automated fix

Run the provided script:

```bash
sudo ./scripts/docker-host-fix.sh install
```

Verify:
```bash
sudo ./scripts/docker-host-fix.sh check
```

Remove:
```bash
sudo ./scripts/docker-host-fix.sh rollback
```

## Port-specific vs. broad rules

| Rule | Scope | Security |
|------|-------|----------|
| `ufw allow from 172.17.0.0/16 to any port 8080` | Single port | More restrictive |
| `ufw allow from 172.17.0.0/16` | All ports | Convenient but broader |

For a typical setup with llama-swap (8080), sillytavern (8000), and bifrost
(8090→8080), use:

```bash
sudo ufw allow from 172.17.0.0/16 to any port 8080,8000
```

## Persistence

Both fixes survive reboots:

- **ufw-docker**: Modifies `/etc/ufw/after.rules` which UFW loads on boot
- **UFW INPUT rule**: Stored in `/etc/ufw/rules.rules` by UFW itself

## Related

- [tailscale-docker.md](tailscale-docker.md) — Tailscale peer → Docker container
  connectivity (separate but related issue, handled by DOCKER-USER chain)
- `scripts/tailscale-docker-fix.sh` — Fix for Tailscale → Docker FORWARD issue
- `scripts/docker-host-fix.sh` — Fix for Docker → host INPUT issue
