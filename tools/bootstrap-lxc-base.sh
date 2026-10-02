#!/bin/bash
# Run as root inside the dedicated bench container, after verifying its identity.
set -euo pipefail
: "${WASMHOME_PUBLIC_KEY_BASE64:?Provide the approved OpenSSH public key as base64}"
[ "$(hostname)" = "wasmhome-bench" ] || { echo 'Unexpected container hostname' >&2; exit 1; }
key=$(printf '%s' "$WASMHOME_PUBLIC_KEY_BASE64" | base64 -d)
case "$key" in
  ssh-ed25519\ *|ssh-rsa\ *|ecdsa-sha2-*\ *) ;;
  *) echo 'Expected an OpenSSH public key' >&2; exit 1 ;;
esac
export DEBIAN_FRONTEND=noninteractive
export LANG=C.UTF-8 LC_ALL=C.UTF-8
update-locale --reset LANG=C.UTF-8
# Keep the bench on IPv4; router RA otherwise triggers a hanging DHCPv6 job.
printf '%s\n' 'net.ipv6.conf.all.disable_ipv6=1' 'net.ipv6.conf.default.disable_ipv6=1' > /etc/sysctl.d/90-wasmhome-ipv4.conf
sysctl -p /etc/sysctl.d/90-wasmhome-ipv4.conf
apt-get update
apt-get install -y --no-install-recommends openssh-server ca-certificates curl
id wasmhome >/dev/null 2>&1 || useradd --create-home --shell /bin/bash wasmhome
install -d -m 700 -o wasmhome -g wasmhome /home/wasmhome/.ssh
printf '%s\n' "$key" > /home/wasmhome/.ssh/authorized_keys
chown wasmhome:wasmhome /home/wasmhome/.ssh/authorized_keys
chmod 600 /home/wasmhome/.ssh/authorized_keys
install -d -m 750 -o wasmhome -g wasmhome /srv/wasmhome/{builds,artifacts,logs}
install -d -m 755 /etc/ssh/sshd_config.d
printf '%s\n' 'PasswordAuthentication no' 'KbdInteractiveAuthentication no' 'PermitRootLogin no' > /etc/ssh/sshd_config.d/00-wasmhome.conf
/usr/sbin/sshd -t
systemctl enable ssh
systemctl restart ssh
printf '%s\n' 'wasmhome-initial-state-v1' > /srv/wasmhome/logs/persistence-check.txt
chown wasmhome:wasmhome /srv/wasmhome/logs/persistence-check.txt
chmod 640 /srv/wasmhome/logs/persistence-check.txt
dpkg-query -W openssh-server ca-certificates curl
