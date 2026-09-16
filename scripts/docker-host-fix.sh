#!/usr/bin/env bash
# Fix Docker container → host services connectivity blocked by UFW.
#
# Background:
#   UFW's INPUT chain has a default-drop policy. When a Docker container
#   tries to reach a service on the host (e.g., llama-swap on bare metal),
#   the traffic hits the INPUT chain with no matching rule → DROP.
#
#   Symptoms: ping works from containers, but TCP connections to 172.17.0.1
#   (docker0 gateway) or the host's LAN/VPN IPs timeout. External HTTP works.
#
#   This is separate from the DOCKER-USER chain (handled by ufw-docker) which
#   controls FORWARD traffic (container-to-container, container-to-external).
#   Traffic to host-bound services uses INPUT, not FORWARD.
#
#   The fix requires:
#   1. ufw-docker for DOCKER-USER chain (Tailscale → Docker)
#   2. A UFW INPUT rule for Docker bridge → host
#
# This script manages the UFW INPUT rule. Use with ufw-docker for full fix.
#
# Usage:
#   sudo ./scripts/docker-host-fix.sh install [--ports PORTS]  # apply fix (default)
#   sudo ./scripts/docker-host-fix.sh check                     # verify rule presence
#   sudo ./scripts/docker-host-fix.sh rollback                 # remove the fix

set -euo pipefail

DOCKER_NET="172.17.0.0/16"
PORTS="${PORTS:-}"  # Optional: comma-separated ports (e.g., "8080,8000")
AFTER_RULES="/etc/ufw/after.rules"
UFW_RULES="/etc/ufw/rules.rules"
TAG="docker-host-fix"
INSTALL_SCRIPT="/usr/local/bin/ufw-docker"

_has_ufw_rule() {
    sudo ufw status numbered 2>/dev/null | grep -qF "${TAG}"
}

_has_after_rules_entry() {
    [[ -f "${AFTER_RULES}" ]] && grep -qF "${TAG}" "${AFTER_RULES}"
}

_has_ufw_rules_entry() {
    [[ -f "${UFW_RULES}" ]] && grep -qF "${TAG}" "${UFW_RULES}"
}

_get_ufw_rule_num() {
    sudo ufw status numbered 2>/dev/null \
        | awk -v tag="${TAG}" '/^\[/ && $0 ~ tag {gsub(/\[|\]|:/, "", $2); print $2; exit}'
}

_add_ufw_rule() {
    if _has_ufw_rule; then
        echo "  UFW rule: already present"
        return
    fi

    local rule_desc="Docker containers to host"
    if [[ -n "${PORTS}" ]]; then
        echo "  UFW rule: adding allow from ${DOCKER_NET} to any port ${PORTS}"
        sudo ufw insert 1 allow from "${DOCKER_NET}" to any port "${PORTS}" comment "${TAG}"
    else
        echo "  UFW rule: adding allow from ${DOCKER_NET} to any (all ports)"
        sudo ufw insert 1 allow from "${DOCKER_NET}" comment "${TAG}"
    fi
}

_add_after_rules_entry() {
    if [[ ! -f "${AFTER_RULES}" ]]; then
        echo "  after.rules: file not found, skipping persistence"
        return
    fi
    if _has_after_rules_entry; then
        echo "  after.rules: entry already present"
        return
    fi
    echo "  after.rules: adding entry for persistence"

    if [[ -n "${PORTS}" ]]; then
        local port_list
        port_list=$(echo "${PORTS}" | tr ',' '\n' | tr -d ' ')
        for port in ${port_list}; do
            echo "-A ufw-user-input -s ${DOCKER_NET} -p tcp --dport ${port} -j ACCEPT -m comment --comment \"${TAG}\"" \
                | sudo tee -a "${AFTER_RULES}" > /dev/null
        done
    else
        echo "-A ufw-user-input -s ${DOCKER_NET} -j ACCEPT -m comment --comment \"${TAG}\"" \
            | sudo tee -a "${AFTER_RULES}" > /dev/null
    fi
    echo "  reloading ufw"
    sudo ufw reload > /dev/null
}

_remove_ufw_rule() {
    if ! _has_ufw_rule; then
        echo "  UFW rule: not present"
        return
    fi

    local rule_num
    rule_num=$(_get_ufw_rule_num)
    if [[ -n "${rule_num}" ]]; then
        echo "  UFW rule: deleting rule ${rule_num}"
        echo y | sudo ufw delete "${rule_num}" > /dev/null
    fi
}

_remove_after_rules_entry() {
    if [[ ! -f "${AFTER_RULES}" ]]; then
        return
    fi
    if ! _has_after_rules_entry; then
        echo "  after.rules: entry not present"
        return
    fi
    echo "  after.rules: removing entries"
    sudo sed -i "/${TAG}/d" "${AFTER_RULES}"
    echo "  reloading ufw"
    sudo ufw reload > /dev/null
}

cmd_check() {
    local ok=true

    echo "Checking Docker → host fix:"
    echo

    if _has_ufw_rule; then
        echo "  UFW INPUT rule:    ✓ present"
        sudo ufw status numbered 2>/dev/null | grep "${TAG}" | head -1
    else
        echo "  UFW INPUT rule:    ✗ MISSING"
        ok=false
    fi

    if _has_after_rules_entry; then
        echo "  after.rules:       ✓ present"
    else
        echo "  after.rules:       ✗ MISSING (won't survive reboot)"
        ok=false
    fi

    echo
    echo "Testing connectivity from container:"
    if command -v docker &>/dev/null; then
        local test_result
        if [[ -n "${PORTS}" ]]; then
            local first_port
            first_port=$(echo "${PORTS}" | cut -d',' -f1 | tr -d ' ')
            test_result=$(docker run --rm --network host alpine sh -c "wget -q -O - --timeout=3 http://127.0.0.1:${first_port}/ 2>&1" || echo "FAILED")
        else
            test_result=$(docker run --rm --network host alpine sh -c "nc -zv 127.0.0.1 22 2>&1" || echo "FAILED")
        fi
        if [[ "$test_result" != "FAILED" ]] && [[ "$test_result" != *"timed out"* ]]; then
            echo "  Connectivity test: ✓ PASSED"
        else
            echo "  Connectivity test: ✗ FAILED"
            ok=false
        fi
    else
        echo "  Connectivity test: ⚠ docker not available"
    fi

    echo
    if $ok; then
        echo "Fix is in place."
        return 0
    else
        echo "Fix is incomplete. Run: sudo $0 install"
        return 1
    fi
}

cmd_install() {
    echo "Installing Docker → host fix:"
    echo "  Docker network: ${DOCKER_NET}"
    [[ -n "${PORTS}" ]] && echo "  Ports: ${PORTS}"
    echo

    _add_ufw_rule
    _add_after_rules_entry

    echo
    echo "Done. Verify with: sudo $0 check"
}

cmd_rollback() {
    echo "Removing Docker → host fix:"
    _remove_ufw_rule
    _remove_after_rules_entry
    echo
    echo "Done. Docker containers will no longer reach host services."
}

show_help() {
    sed -n '2,25p' "$0"
}

main() {
    local cmd="${1:-install}"
    local ports_arg=""

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            install|check|rollback)
                cmd="$1"
                ;;
            --ports|-p)
                shift
                PORTS="$1"
                ;;
            -h|--help)
                show_help
                exit 0
                ;;
            *)
                echo "Unknown option: $1"
                show_help
                exit 2
                ;;
        esac
        shift
    done

    case "$cmd" in
        install)  cmd_install ;;
        check)    cmd_check ;;
        rollback) cmd_rollback ;;
        *)
            echo "Usage: sudo $0 [install|check|rollback] [--ports PORTS]"
            exit 2
            ;;
    esac
}

main "$@"
