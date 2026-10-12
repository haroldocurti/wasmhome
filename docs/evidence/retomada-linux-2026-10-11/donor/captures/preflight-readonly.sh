set -u
printf 'IDENTITY\n'
id -u; id -un; uname -m; hostname
printf 'TOOLS\n'
for task_tool in git python3 tar xz cmake ninja gcc g++ make curl; do command -v "$task_tool" || true; done
printf 'PACKAGES\n'
dpkg-query -W -f='${Package} ${Version}\n' git python3 tar xz-utils build-essential cmake ninja-build gcc g++ make zlib1g-dev libzstd-dev libxml2-dev 2>/dev/null || true
printf 'NETWORK\n'
ip route show default || true
timeout 10 getent ahostsv4 github.com || true
printf 'MINIMAL_APT\n'
apt-get -s --no-install-recommends install gcc g++ cmake ninja-build
