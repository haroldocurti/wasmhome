"""Pure guard tests. Do not install, configure, build or mutate a donor."""
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import donor_wamrc as donor
import proxmox_donor_guard as host


class DonorTests(unittest.TestCase):
    def test_wsl_physical_disk_gate_uses_host_not_virtual_free(self):
        sample = dict(c_free=14 * donor.GIB, windows_memory_free=6 * donor.GIB,
                      donor_filesystem_available=900 * donor.GIB, donor_memory_available=13 * donor.GIB, donor_swap_used=0)
        with patch.object(donor, "PROFILE", "wsl"), self.assertRaisesRegex(ValueError, "physical C"):
            donor.resource_gate(sample, initial=True)

    def test_ct_guest_swap_and_memory_gates(self):
        sample = dict(donor_filesystem_available=30 * donor.GIB, donor_memory_available=7 * donor.GIB, donor_swap_used=0)
        with patch.object(donor, "PROFILE", "lxc110"):
            donor.resource_gate(sample, initial=True)
            sample["donor_swap_used"] = 1
            with self.assertRaisesRegex(ValueError, "swap"):
                donor.resource_gate(sample, initial=True)

    def test_initial_host_memory_is_blocking_even_with_ample_disk(self):
        with patch.object(host.platform, "node", return_value="haroldo"), patch.object(host, "meminfo", return_value=(2 * host.GIB, 5 * host.GIB)), patch.object(host, "guest_states", return_value={"ct109": "stopped"}), patch.object(host, "run", return_value="110"), patch.object(host, "devserver_free", return_value=189 * host.GIB), patch.object(pathlib.Path, "exists", return_value=False), self.assertRaisesRegex(ValueError, "8GiB"):
            host.check("before-create")

    def test_existing_ct110_is_never_replaced(self):
        with patch.object(host.platform, "node", return_value="haroldo"), patch.object(host, "meminfo", return_value=(12 * host.GIB, 0)), patch.object(host, "guest_states", return_value={"ct109": "stopped"}), patch.object(pathlib.Path, "exists", return_value=True), self.assertRaisesRegex(ValueError, "no replacement"):
            host.check("before-create")

    def test_ct109_is_not_started_or_automatically_stopped_by_guard(self):
        with patch.object(host.platform, "node", return_value="haroldo"), patch.object(host, "meminfo", return_value=(12 * host.GIB, 0)), patch.object(host, "guest_states", return_value={"ct109": "running"}), self.assertRaisesRegex(ValueError, "does not stop"):
            host.check("before-create")

    def test_own_process_group_identity_must_match_before_signal(self):
        class Child:
            pid = 123
            def poll(self): return None
        with patch.object(donor.os, "getpgid", return_value=456), patch.object(donor.os, "killpg") as kill, self.assertRaisesRegex(ValueError, "no signal"):
            donor.Runner.interrupt(Child())
        kill.assert_not_called()

    def test_ct_stale_host_lease_rejects_build_without_guest_actions(self):
        lease = dict(schema="wasmhome-proxmox-donor-lease-v1", ctid=110, hostname=host.NAME,
                     ct_state="running", timestamp_epoch=1)
        with patch.object(donor, "PROFILE", "lxc110"), patch.object(donor, "HOST_LEASE", pathlib.Path("lease.json")), patch.object(pathlib.Path, "is_file", return_value=True), patch.object(donor, "strict_json", return_value=lease), self.assertRaisesRegex(ValueError, "stale"):
            donor.resources(pathlib.Path.home())

    def test_template_bytes_matching_but_hash_wrong_rejects(self):
        with tempfile.TemporaryDirectory() as directory:
            template = pathlib.Path(directory) / "template.tar.zst"
            template.write_bytes(b"wrong")
            with patch.object(host, "TEMPLATE", template), patch.object(host, "TEMPLATE_BYTES", 5), self.assertRaisesRegex(ValueError, "digest changed"):
                host.template_identity()

    def test_final_state_requires_new_ct_stopped_and_other_states_unchanged(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            baseline = pathlib.Path(directory) / "before-start.json"
            baseline.write_text(json.dumps(dict(ctid=110, hostname=host.NAME, phase="before-start", config_sha256="abc", other_guest_states={"ct109": "stopped", "vm103": "running"})))
            with patch.object(host.platform, "node", return_value="haroldo"), patch.object(host, "meminfo", return_value=(3 * host.GIB, 0)), patch.object(host, "guest_states", return_value={"ct109": "stopped", "vm103": "running"}), patch.object(host, "configuration", return_value="abc"), patch.object(host, "ct_state", return_value="running"), self.assertRaisesRegex(ValueError, "only new CT110 stopped"):
                host.check("final", baseline)

    def test_duplicate_approval_fields_reject(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = pathlib.Path(directory) / "approval.json"
            receipt.write_text('{"status":"pending","status":"approved"}')
            with self.assertRaisesRegex(ValueError, "duplicate"):
                donor.strict_json(receipt)

    def test_configuration_allows_only_documented_create_defaults(self):
        configuration = "arch: amd64\ncores: 2\nhostname: wasmhome-toolchain-donor\nmemory: 8192\nnet0: name=eth0,bridge=vmbr0,hwaddr=BC:24:11:00:00:11,ip=dhcp,type=veth\nonboot: 0\nostype: debian\nrootfs: DevServer:vm-110-disk-0,size=32G\nswap: 0\nunprivileged: 1\n"
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "110.conf"
            path.write_text(configuration)
            with patch.object(host.pathlib, "Path", return_value=path):
                self.assertEqual(len(host.configuration()), 64)
            for extra in ("mp0: /srv/private,mp=/host\n", "lxc.mount.entry: /srv/private host none bind 0 0\n", "net1: name=eth1,bridge=vmbr0,ip=dhcp\n", "hookscript: local:snippets/hook.pl\n", "lxc.cgroup2.devices.allow: a\n", "dev0: /dev/kvm\n", "features: nesting=1\n", "unknown: value\n"):
                with self.subTest(extra=extra):
                    path.write_text(configuration + extra)
                    with patch.object(host.pathlib, "Path", return_value=path), self.assertRaisesRegex(ValueError, "unapproved extra"):
                        host.configuration()

    def test_configuration_rejects_extra_rootfs_and_net_options(self):
        configuration = "arch: amd64\ncores: 2\nhostname: wasmhome-toolchain-donor\nmemory: 8192\nnet0: name=eth0,bridge=vmbr0,hwaddr=BC:24:11:00:00:11,ip=dhcp,type=veth\nostype: debian\nrootfs: DevServer:vm-110-disk-0,size=32G\nswap: 0\nunprivileged: 1\n"
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "110.conf"
            for altered in (configuration.replace("size=32G", "size=32G,ro=0"), configuration.replace("vm-110-disk-0", "vm-107-disk-0"), configuration.replace("type=veth", "type=veth,tag=2"), configuration.replace("type=veth", "type=veth,ip=dhcp")):
                with self.subTest(config=altered):
                    path.write_text(altered)
                    with patch.object(host.pathlib, "Path", return_value=path), self.assertRaises(ValueError):
                        host.configuration()


if __name__ == "__main__":
    unittest.main()
