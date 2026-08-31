#!/usr/bin/env bash
# Fix Tailscale → Docker container connectivity blocked by ufw-docker.
#
# Background:
#   ufw-docker (installed by Omarchy's first-run firewall setup) adds a
#   DOCKER-USER chain rule that drops traffic from "non-private" sources to
#   RFC 1918 private ranges (incl. Docker's bridge subnet 172.17.0.0/16).
#   Its anti-spoofing heuristic sees Tailscale CGNAT traffic (100.64.0.0/10)
#   hitting the Docker bridge and assumes spoofing, dropping the packet
#   before it can reach the container via FORWARD chain.
#
#   Symptom: a Docker container is reachable on the host (127.0.0.1 / LAN IP)
#   and from the host via its own Tailscale IP, but NOT from any Tailscale
#   peer (phone, another machine). Connections time out silently with no log
#   line in the container, because the packet is dropped by the kernel before
#   delivery.
#
# This script adds a RETURN rule at the top of DOCKER-USER that whitelists
# Tailscale's CGNAT range, so it bypasses ufw-docker's logging-deny rules.
# Idempotent: safe to re-run. Requires sudo.
#
# Usage:
#   sudo ./scripts/tailscale-docker-fix.sh install   # apply fix (default)
#   sudo ./scripts/tailscale-docker-fix.sh check     # verify rule presence
#   sudo ./scripts/tailscale-docker-fix.sh rollback  # remove the fix

set -euo pipefail

DOCKER_USER_CHAIN="DOCKER-USER"
TAILSCALE_CGNAT="100.64.0.0/10"
PRIVATE_RANGES="172.16.0.0/12"
AFTER_RULES="/etc/ufw/after.rules"
BEGIN_MARKER="# BEGIN UFW AND DOCKER"
END_MARKER="# END UFW AND DOCKER"

# Comment tag marks the rule so the script can find/remove it later.
TAG="tailscale-docker-fix"
RULE_LINE="-A ${DOCKER_USER_CHAIN} -s ${TAILSCALE_CGNAT} -d ${PRIVATE_RANGES} -m comment --comment \"${TAG}\" -j RETURN"

_has_runtime_rule() {
    sudo iptables -S "${DOCKER_USER_CHAIN}" 2>/dev/null | grep -qF "${TAG}"
}

_has_persistent_rule() {
    [[ -f "${AFTER_RULES}" ]] && grep -qF "${TAG}" "${AFTER_RULES}"
}

_add_runtime_rule() {
    if _has_runtime_rule; then
        echo "  runtime rule: already present"
        return
    fi
    echo "  runtime rule: adding to ${DOCKER_USER_CHAIN} (position 1)"
    sudo iptables -I "${DOCKER_USER_CHAIN}" 1 \
        -s "${TAILSCALE_CGNAT}" -d "${PRIVATE_RANGES}" \
        -m comment --comment "${TAG}" -j RETURN
}

_add_persistent_rule() {
    if [[ ! -f "${AFTER_RULES}" ]]; then
        echo "  persistent rule: ${AFTER_RULES} not found, skipping (will not survive reboot)"
        return
    fi
    if _has_persistent_rule; then
        echo "  persistent rule: already present in ${AFTER_RULES}"
        return
    fi
    # Insert as the first -A DOCKER-USER rule in the UFW+DOCKER block, so it
    # matches before any ufw-docker logging-deny rule.
    echo "  persistent rule: inserting into ${AFTER_RULES}"
    sudo sed -i "/${BEGIN_MARKER}/,/${END_MARKER}/{
        /^-A ${DOCKER_USER_CHAIN} -j ufw-user-forward\$/i\\
${RULE_LINE}
    }" "${AFTER_RULES}"
    echo "  reloading ufw to apply"
    sudo ufw reload >/dev/null
}

_remove_runtime_rule() {
    while sudo iptables -S "${DOCKER_USER_CHAIN}" 2>/dev/null | grep -qF "${TAG}"; do
        local rule_num
        rule_num=$(sudo iptables -L "${DOCKER_USER_CHAIN}" -n --line-numbers \
            | awk -v tag="${TAG}" '$0 ~ tag {print $1; exit}')
        echo "  runtime rule: removing position ${rule_num}"
        sudo iptables -D "${DOCKER_USER_CHAIN}" "${rule_num}"
    done
}

_remove_persistent_rule() {
    if [[ ! -f "${AFTER_RULES}" ]]; then
        return
    fi
    if ! _has_persistent_rule; then
        echo "  persistent rule: not present"
        return
    fi
    echo "  persistent rule: removing from ${AFTER_RULES}"
    # Delete every line containing the tag inside the UFW+DOCKER block.
    sudo sed -i "/${BEGIN_MARKER}/,/${END_MARKER}/{/${TAG}/d;}" "${AFTER_RULES}"
    echo "  reloading ufw"
    sudo ufw reload >/dev/null
}

cmd_check() {
    local ok=true
    if _has_runtime_rule; then
        echo "  runtime:    ✓ present"
    else
        echo "  runtime:    ✗ MISSING"
        ok=false
    fi
    if _has_persistent_rule; then
        echo "  persistent: ✓ present in ${AFTER_RULES}"
    else
        echo "  persistent: ✗ MISSING from ${AFTER_RULES}"
        ok=false
    fi
    echo
    echo "Live DOCKER-USER chain (filtered for ${TAILSCALE_CGNAT}):"
    sudo iptables -L "${DOCKER_USER_CHAIN}" -n -v \
        | { head -1; grep -F "${TAILSCALE_CGNAT}" || echo "  (no matching rule)"; } \
        || true
    if $ok; then
        return 0
    else
        echo
        echo "Fix missing. Run: sudo $0 install"
        return 1
    fi
}

cmd_install() {
    echo "Applying Tailscale → Docker fix:"
    _add_runtime_rule
    _add_persistent_rule
    echo
    echo "Done. Verify with: sudo $0 check"
}

cmd_rollback() {
    echo "Removing Tailscale → Docker fix:"
    _remove_runtime_rule
    _remove_persistent_rule
    echo
    echo "Done. Fix is gone. Tailscale peers will again time out to Docker."
}

case "${1:-install}" in
    install)  cmd_install ;;
    check)    cmd_check ;;
    rollback) cmd_rollback ;;
    -h|--help)
        sed -n '2,30p' "$0"
        ;;
    *)
        echo "Usage: sudo $0 [install|check|rollback]" >&2
        exit 2
        ;;
esac