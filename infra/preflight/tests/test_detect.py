"""Unit tests for the detectors."""

from __future__ import annotations

import unittest

from helpers import TempRootCase, runner

from pinka_preflight.detect import (
    conflicts,
    environment,
    gpu,
    hardware,
    kernel,
    os_release,
    timesync,
)


class OsReleaseTest(TempRootCase):
    def test_parse_quotes_and_comments(self) -> None:
        raw = os_release.parse_os_release(
            '# comment\nNAME="Example Linux"\nID=example\nID_LIKE="debian ubuntu"\n'
            'VERSION_ID=\'13\'\nPRETTY_NAME="With \\"quote\\""\nbroken line\nlower=x\n'
        )
        self.assertEqual(raw["NAME"], "Example Linux")
        self.assertEqual(raw["ID"], "example")
        self.assertEqual(raw["VERSION_ID"], "13")
        self.assertEqual(raw["PRETTY_NAME"], 'With "quote"')
        self.assertNotIn("lower", raw)

    def test_detect_with_fallback_path(self) -> None:
        self.put("/usr/lib/os-release", 'ID="Fedora"\nVERSION_ID=44\nID_LIKE=""\n')
        osr = os_release.detect_os_release(self.fs())
        assert osr is not None
        self.assertEqual(osr.id, "fedora")
        self.assertEqual(osr.id_like, [])
        self.assertEqual(osr.version_major, "44")

    def test_missing_os_release(self) -> None:
        self.assertIsNone(os_release.detect_os_release(self.fs()))

    def test_defaults(self) -> None:
        self.put("/etc/os-release", "NAME=x\n")
        osr = os_release.detect_os_release(self.fs())
        assert osr is not None
        self.assertEqual(osr.id, "linux")
        self.assertIsNone(osr.version_major)


def signals(**overrides: environment.SignalValue) -> dict[str, environment.SignalValue]:
    base: dict[str, environment.SignalValue] = {
        "osrelease": "6.8.0-85-generic",
        "osrelease_microsoft": False,
        "osrelease_wsl2": False,
        "wsl1_kernel": False,
        "dev_dxg": False,
        "usr_lib_wsl": False,
        "wsl_interop": None,
        "wsl_conf": False,
        "systemd_detect_virt": "none",
        "systemd_detect_virt_vm": "none",
        "systemd_detect_virt_container": "none",
        "cpu_hypervisor_flag": False,
        "dmi_sys_vendor": "Example Board Vendor",
        "dmi_product_name": "Desktop",
        "dmi_board_vendor": None,
        "dmi_bios_vendor": None,
        "dockerenv": False,
        "containerenv": False,
        "pid1_container_env": None,
        "cgroup_container_hint": None,
    }
    base.update(overrides)
    return base


class EnvironmentTest(TempRootCase):
    def test_baremetal_high_confidence(self) -> None:
        env = environment.classify(signals())
        self.assertEqual((env.env_class, env.confidence), ("baremetal", "high"))

    def test_wsl_is_never_a_container(self) -> None:
        env = environment.classify(
            signals(
                systemd_detect_virt="wsl",
                systemd_detect_virt_container="wsl",
                osrelease_microsoft=True,
                osrelease="6.6.87.2-microsoft-standard-WSL2",
                osrelease_wsl2=True,
                usr_lib_wsl=True,
            )
        )
        self.assertEqual(env.env_class, "wsl2")
        self.assertEqual(env.hypervisor, "microsoft")

    def test_wsl_reported_without_microsoft_kernel_is_a_conflict(self) -> None:
        env = environment.classify(
            signals(systemd_detect_virt="wsl", systemd_detect_virt_container="wsl")
        )
        self.assertEqual(env.env_class, "wsl2")
        self.assertEqual(env.confidence, "low")
        self.assertTrue(env.conflicts_between_signals)

    def test_wsl1_kernel(self) -> None:
        env = environment.classify(
            signals(
                osrelease="4.4.0-19041-Microsoft",
                osrelease_microsoft=True,
                wsl1_kernel=True,
                systemd_detect_virt=None,
            )
        )
        self.assertEqual(env.env_class, "wsl1")

    def test_container_markers(self) -> None:
        for marker in (
            {"dockerenv": True},
            {"containerenv": True},
            {"pid1_container_env": "podman"},
            {"systemd_detect_virt_container": "lxc"},
            {"cgroup_container_hint": "kubepods"},
        ):
            self.assertEqual(environment.classify(signals(**marker)).env_class, "container", marker)

    def test_vm_with_matching_dmi(self) -> None:
        env = environment.classify(
            signals(systemd_detect_virt_vm="kvm", cpu_hypervisor_flag=True, dmi_sys_vendor="QEMU")
        )
        self.assertEqual((env.env_class, env.hypervisor, env.confidence), ("vm", "kvm", "high"))

    def test_vm_with_conflicting_dmi(self) -> None:
        env = environment.classify(
            signals(
                systemd_detect_virt_vm="vmware", cpu_hypervisor_flag=True, dmi_sys_vendor="QEMU"
            )
        )
        self.assertEqual(env.env_class, "vm")
        self.assertTrue(env.conflicts_between_signals)

    def test_unknown_hypervisor_name(self) -> None:
        self.assertEqual(
            environment.classify(signals(systemd_detect_virt_vm="bhyve")).hypervisor, "other"
        )

    def test_sdv_none_but_one_vm_signal(self) -> None:
        env = environment.classify(signals(cpu_hypervisor_flag=True))
        self.assertEqual((env.env_class, env.confidence), ("baremetal", "low"))

    def test_without_systemd_detect_virt(self) -> None:
        none = {
            "systemd_detect_virt": None,
            "systemd_detect_virt_vm": None,
            "systemd_detect_virt_container": None,
        }
        self.assertEqual(environment.classify(signals(**none)).confidence, "medium")
        self.assertEqual(
            environment.classify(
                signals(**none, cpu_hypervisor_flag=True, dmi_sys_vendor="VMware, Inc.")
            ).env_class,
            "vm",
        )
        self.assertEqual(
            environment.classify(signals(**none, cpu_hypervisor_flag=True)).hypervisor, "other"
        )
        self.assertEqual(
            environment.classify(signals(**none, dmi_sys_vendor=None)).env_class, "unknown"
        )

    def test_microsoft_hardware_is_not_a_vm(self) -> None:
        self.assertIsNone(
            environment.dmi_hypervisor(
                signals(dmi_sys_vendor="Microsoft Corporation", dmi_product_name="Surface Laptop")
            )
        )
        self.assertEqual(
            environment.dmi_hypervisor(
                signals(dmi_sys_vendor="Microsoft Corporation", dmi_product_name="Virtual Machine")
            ),
            "microsoft",
        )

    def test_wsl_conf_defaults_and_values(self) -> None:
        defaults = environment.parse_wsl_conf(None)
        self.assertFalse(defaults.present)
        self.assertFalse(defaults.systemd)
        self.assertTrue(defaults.automount)
        conf = environment.parse_wsl_conf(
            "[boot]\nsystemd=true\n[interop]\nenabled=false\n[network]\nhostname=pinka-lab\n"
        )
        self.assertTrue(conf.systemd)
        self.assertFalse(conf.interop_enabled)
        self.assertEqual(conf.hostname, "pinka-lab")
        conf.interop_registered = "enabled"
        self.assertTrue(conf.interop_enabled)
        self.assertFalse(environment.parse_wsl_conf("not an ini [").systemd)

    def test_detect_collects_signals_from_fs(self) -> None:
        self.put("/proc/sys/kernel/osrelease", "6.6.87.2-microsoft-standard-WSL2\n")
        self.put("/proc/sys/fs/binfmt_misc/WSLInterop", "disabled\n")
        self.put("/proc/1/environ", b"container=podman\0HOME=/\0")
        self.put("/etc/wsl.conf", "[boot]\nsystemd=true\n")
        env = environment.detect_environment(self.fs(), runner({"systemd-detect-virt": "wsl\n"}))
        self.assertEqual(env.env_class, "wsl2")
        self.assertEqual(env.signals["pid1_container_env"], "podman")
        assert env.wsl is not None
        self.assertFalse(env.wsl.interop_enabled)
        self.assertTrue(env.wsl.systemd)


class KernelTest(TempRootCase):
    def test_module_states(self) -> None:
        self.put("/proc/modules", "overlay 1 0 - Live 0x0\n")
        self.mkdir("/sys/module/veth")
        r = runner({"modprobe -n nf_tables": ""})
        fs = self.fs()
        loaded = kernel.loaded_modules(fs)
        self.assertEqual(kernel.module_state(fs, r, "overlay", loaded), "loaded")
        self.assertEqual(kernel.module_state(fs, r, "veth", loaded), "builtin")
        self.assertEqual(kernel.module_state(fs, r, "nf_tables", loaded), "loadable")
        self.assertEqual(kernel.module_state(fs, r, "btrfs", loaded), "missing")

    def test_secure_boot_sources(self) -> None:
        fs = self.fs()
        self.assertEqual(
            kernel.detect_secure_boot(fs, runner({"mokutil --sb-state": "SecureBoot enabled\n"})),
            "enabled",
        )
        self.assertEqual(kernel.detect_secure_boot(fs, runner()), "not_efi")
        self.mkdir("/sys/firmware/efi")
        self.assertEqual(kernel.detect_secure_boot(fs, runner()), "unknown")
        self.put("/sys/firmware/efi/efivars/SecureBoot-test", b"\x06\x00\x00\x00\x00")
        self.assertEqual(kernel.detect_secure_boot(fs, runner()), "disabled")
        self.put("/sys/firmware/efi/efivars/SecureBoot-test", b"\x06\x00\x00\x00\x01")
        self.assertEqual(kernel.detect_secure_boot(fs, runner()), "enabled")

    def test_mac_detection(self) -> None:
        fs = self.fs()
        self.assertEqual(kernel.detect_mac(fs, runner()), ("none", None))
        self.assertEqual(
            kernel.detect_mac(fs, runner({"getenforce": "Permissive\n"})), ("selinux", "permissive")
        )
        self.mkdir("/sys/kernel/security/apparmor")
        self.assertEqual(kernel.detect_mac(fs, runner()), ("apparmor", "enabled"))
        self.put("/sys/module/apparmor/parameters/enabled", "N\n")
        self.assertEqual(kernel.detect_mac(fs, runner()), ("apparmor", "disabled"))
        self.put("/sys/fs/selinux/enforce", "1")
        self.assertEqual(kernel.detect_mac(fs, runner()), ("selinux", "enforcing"))

    def test_cgroup2_requires_controllers_and_fstype(self) -> None:
        self.put("/proc/1/comm", "systemd\n")
        self.put("/sys/fs/cgroup/cgroup.controllers", "cpu memory\n")
        info = kernel.detect_kernel(self.fs(), runner({"stat -f -c %T /sys/fs/cgroup": "tmpfs\n"}))
        self.assertFalse(info.cgroup2)
        self.assertEqual(info.cgroup2_controllers, [])
        info = kernel.detect_kernel(
            self.fs(), runner({"stat -f -c %T /sys/fs/cgroup": "cgroup2fs\n"})
        )
        self.assertTrue(info.cgroup2)
        self.assertEqual(info.cgroup2_controllers, ["cpu", "memory"])
        self.assertTrue(info.systemd_pid1)
        self.assertIsNone(info.system_state)

    def test_feature_present_via_filesystems(self) -> None:
        self.put("/proc/filesystems", "nodev\toverlay\n\tbtrfs\n")
        info = kernel.detect_kernel(self.fs(), runner())
        self.assertTrue(info.feature_present("btrfs"))
        self.assertFalse(info.module_available("btrfs"))
        self.assertFalse(info.feature_present("veth"))


class HardwareTest(TempRootCase):
    def test_parsers(self) -> None:
        mem = hardware.parse_meminfo("MemTotal: 2097152 kB\nHugePages_Total: 0\nbad\n")
        self.assertEqual(mem["MemTotal"], 2 * 1024**3)
        self.assertEqual(mem["HugePages_Total"], 0)
        self.assertIsNone(hardware.parse_cpu_max("max 100000"))
        self.assertEqual(hardware.parse_cpu_max("200000 100000"), 2.0)
        self.assertIsNone(hardware.parse_cpu_max("x y"))
        self.assertIsNone(hardware.parse_cpu_max(None))

    def test_nearest_existing_and_disk_info(self) -> None:
        self.mkdir("/srv")
        fs = self.fs()
        self.assertEqual(hardware.nearest_existing(fs, "/srv/pinka/data"), "/srv")
        self.assertEqual(hardware.nearest_existing(fs, "/missing"), "/")
        r = runner(
            {
                "df --output=fstype,size,avail -B1 /srv": "Type 1B-blocks Avail\nbtrfs 2147483648 1073741824\n"
            }
        )
        d = hardware.disk_info(fs, r, "/srv/pinka")
        self.assertEqual(
            (d.checked_path, d.fstype, d.size_gib, d.free_gib), ("/srv", "btrfs", 2.0, 1.0)
        )
        self.assertIsNone(hardware.disk_info(fs, runner(), "/srv").free_gib)

    def test_quota_limits_threads_and_block_devices(self) -> None:
        self.put("/sys/fs/cgroup/cpu.max", "400000 100000\n")
        self.put("/sys/block/nvme0n1/size", "2048\n")
        self.put("/sys/block/nvme0n1/queue/rotational", "0\n")
        self.put("/sys/block/nvme0n1/device/model", "Example NVMe\n")
        self.put("/sys/block/loop0/size", "8\n")
        hw = hardware.detect_hardware(self.fs(), runner(cpu_count=24), "/srv/pinka")
        self.assertEqual(hw.cpu_threads_effective, 4)
        self.assertEqual([b.name for b in hw.block_devices], ["nvme0n1"])
        self.assertEqual(hw.block_devices[0].model, "Example NVMe")
        self.assertFalse(hw.block_devices[0].rotational)
        self.assertIsNone(hw.memory_gib)


class GpuTest(TempRootCase):
    def test_parse_nvidia_smi(self) -> None:
        gpus = gpu.parse_nvidia_smi("A, 8192 MiB, 1.0, 8.6\nB, [N/A], 1.0, 7.5\nbroken\n")
        self.assertEqual(gpus[0], ("A", 8.0, "1.0", "8.6"))
        self.assertIsNone(gpus[1][1])
        self.assertEqual(len(gpus), 2)

    def test_amd_is_reported_unsupported_and_vm_without_passthrough(self) -> None:
        self.put("/sys/bus/pci/devices/0000:03:00.0/vendor", "0x1002\n")
        self.put("/sys/bus/pci/devices/0000:03:00.0/class", "0x030000\n")
        info = gpu.detect_gpu(self.fs(), runner(), "vm")
        self.assertFalse(info.present)
        self.assertEqual(info.unsupported_devices, ["0000:03:00.0 (AMD)"])
        self.assertIn("no GPU passthrough", info.notes)

    def test_installed_but_not_loaded(self) -> None:
        self.put("/sys/bus/pci/devices/0000:01:00.0/vendor", "0x10de\n")
        self.put("/sys/bus/pci/devices/0000:01:00.0/class", "0x030200\n")
        info = gpu.detect_gpu(self.fs(), runner({"modinfo nvidia": "filename: x\n"}), "baremetal")
        self.assertEqual(
            (info.present, info.driver_state, info.usable), (True, "installed_not_loaded", False)
        )

    def test_wsl_without_dxg_and_other_classes(self) -> None:
        info = gpu.detect_gpu(self.fs(), runner(), "wsl2")
        self.assertFalse(info.present)
        self.assertTrue(info.probed)
        self.assertFalse(gpu.detect_gpu(self.fs(), runner(), "container").probed)

    def test_wsl_linux_driver_found_by_dkms(self) -> None:
        info = gpu.detect_gpu(
            self.fs(), runner({"dkms status": "nvidia/580, 6.6: installed\n"}), "wsl2"
        )
        self.assertTrue(info.linux_driver_in_wsl)


class ConflictsTest(TempRootCase):
    def detect(self, r: object = None, **kw: object) -> list[tuple[str, str]]:
        args = {
            "env_class": "baremetal",
            "os_id": "ubuntu",
            "systemd_pid1": True,
            "gpu_linux_driver_in_wsl": False,
            "adopt_incus": False,
        }
        args.update(kw)
        report = conflicts.detect_conflicts(self.fs(), r or runner(), **args)  # type: ignore[arg-type]
        return [(c.id, c.severity) for c in report.conflicts]

    def test_clean_host(self) -> None:
        self.assertEqual(self.detect(), [])

    def test_docker_desktop_via_path_and_context(self) -> None:
        self.assertEqual(
            self.detect(runner(which={"docker": "/mnt/wsl/docker-desktop/cli/docker"})),
            [("docker_desktop_wsl", "blocking")],
        )
        r = runner(
            {"docker context ls --format '{{.Name}}'": "default\ndesktop-linux\n"},
            which={"docker": "/usr/bin/docker"},
        )
        self.assertEqual(self.detect(r), [("docker_desktop_wsl", "blocking")])

    def test_snaps_lxd_podman_and_firewall(self) -> None:
        r = runner(
            {
                "snap list docker": "docker\n",
                "snap list lxd": "lxd\n",
                "systemctl is-active podman.socket": "active\n",
                "systemctl is-active ufw": "active\n",
            }
        )
        report = conflicts.detect_conflicts(
            self.fs(),
            r,
            env_class="baremetal",
            os_id="ubuntu",
            systemd_pid1=True,
            gpu_linux_driver_in_wsl=False,
            adopt_incus=False,
        )
        ids = [(c.id, c.severity) for c in report.conflicts]
        self.assertEqual(
            ids,
            [
                ("snap_docker", "blocking"),
                ("lxd_installed", "blocking"),
                ("podman_socket", "warning"),
            ],
        )
        self.assertEqual(report.firewall_backend, "ufw")
        self.assertTrue(report.infos)

    def test_immutable_variants(self) -> None:
        self.assertEqual(self.detect(os_id="ubuntu-core"), [("immutable_os", "blocking")])
        self.put("/usr/sbin/transactional-update")
        self.put("/proc/mounts", "/dev/sda2 / btrfs ro,relatime 0 0\n")
        self.assertEqual(self.detect(), [("immutable_os", "blocking")])

    def test_non_systemd_is_not_reported_on_wsl2(self) -> None:
        self.assertEqual(self.detect(systemd_pid1=False), [("non_systemd_init", "blocking")])
        self.assertEqual(self.detect(systemd_pid1=False, env_class="wsl2"), [])

    def test_musl_via_ldd(self) -> None:
        self.assertEqual(
            self.detect(runner({"ldd --version": "musl libc (x86_64)\n"})),
            [("musl_libc", "blocking")],
        )

    def test_package_manager_busy(self) -> None:
        self.put("/var/lib/dpkg/updates/0001", "x")
        self.assertEqual(self.detect(), [("package_manager_busy", "needs_action")])

    def test_zypper_pid_and_processes(self) -> None:
        self.put("/run/zypp.pid", "4242\n")
        self.mkdir("/proc/4242")
        self.assertEqual(self.detect(), [("package_manager_busy", "needs_action")])

    def test_pkg_process_and_pacman_lock(self) -> None:
        self.put("/proc/77/comm", "dnf\n")
        self.assertEqual(self.detect(), [("package_manager_busy", "needs_action")])
        self.put("/var/lib/pacman/db.lck")
        self.assertEqual(self.detect(), [("package_manager_busy", "needs_action")])

    def test_unmanaged_docker_config(self) -> None:
        self.put("/etc/docker/daemon.json", "{}\n")
        self.assertEqual(self.detect(), [("unmanaged_docker_config", "warning")])
        self.put("/etc/docker/daemon.json", "# Managed by pinka (x)\n{}\n")
        self.assertEqual(self.detect(), [])

    def test_unmanaged_incus_and_adoption(self) -> None:
        self.put("/var/lib/incus/database/global/db.bin", "x")
        r = runner(which={"incus": "/usr/bin/incus"})
        self.assertEqual(self.detect(r), [("unmanaged_incus", "blocking")])
        self.assertEqual(self.detect(r, adopt_incus=True), [("unmanaged_incus", "warning")])
        self.put("/etc/pinka/managed-incus")
        self.assertEqual(self.detect(r), [])

    def test_linux_driver_in_wsl(self) -> None:
        self.assertEqual(
            self.detect(gpu_linux_driver_in_wsl=True, env_class="wsl2"),
            [("linux_nvidia_driver_in_wsl", "blocking")],
        )


class TimeSyncTest(unittest.TestCase):
    def test_timesyncd_offset_units(self) -> None:
        for text, value in (("+1.250ms", 0.00125), ("-3us", -3e-6), ("2s", 2.0), ("+1min", 60.0)):
            ts = timesync.detect_timesync(
                runner(
                    {
                        "timedatectl show": "NTPSynchronized=yes\nNTP=no\n",
                        "timedatectl timesync-status": f"       Offset: {text}\n",
                    }
                )
            )
            self.assertAlmostEqual(ts.offset_s or 0.0, value)
            self.assertEqual(ts.source, "timesyncd")
            self.assertTrue(ts.ntp_synchronized)
            self.assertFalse(ts.ntp_service)

    def test_chrony_fast_and_slow(self) -> None:
        for word, sign in (("fast", 1), ("slow", -1)):
            ts = timesync.detect_timesync(
                runner(
                    {
                        "chronyc -n tracking": f"System time     : 0.000500000 seconds {word} of NTP time\n"
                    }
                )
            )
            self.assertAlmostEqual(ts.offset_s or 0.0, sign * 0.0005)
            self.assertEqual(ts.source, "chrony")

    def test_nothing_available(self) -> None:
        ts = timesync.detect_timesync(runner())
        self.assertIsNone(ts.ntp_synchronized)
        self.assertIsNone(ts.offset_s)


if __name__ == "__main__":
    unittest.main()
