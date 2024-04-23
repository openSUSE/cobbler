"""
This is some of the code behind 'cobbler sync'.

Copyright 2006-2009, Red Hat, Inc and Others
Michael DeHaan <michael.dehaan AT gmail>
John Eckersberg <jeckersb@redhat.com>

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
02110-1301  USA
"""
import shutil
import time
from typing import TYPE_CHECKING, Any, Dict, Optional

from cobbler import utils
from cobbler.enums import Archs
from cobbler.manager import ManagerModule

if TYPE_CHECKING:
    from cobbler.items.distro import Distro
    from cobbler.items.system import System, NetworkInterface

MANAGER = None


def register() -> str:
    """
    The mandatory Cobbler module registration hook.
    """
    return "manage"


class _IscManager(ManagerModule):

    @staticmethod
    def what() -> str:
        """
        Static method to identify the manager.

        :return: Always "isc".
        """
        return "isc"

    def __init__(self, api):
        super().__init__(api)

        self.settings_file_v4 = utils.dhcpconf_location(utils.DHCP.V4)
        self.settings_file_v6 = utils.dhcpconf_location(utils.DHCP.V6)

        self.config = {}  # cache config to allow adding systems incrementally
        self.generic_entry_cnt = 0

    def sync_single_system(self, system: "System"):
        """
        Update the config with data for a single system, write it to the filesysemt, and restart DHCP service.
        :param system: System object to generate the config for.
        """
        if not self.config:
            # cache miss, need full sync for consistent data
            return self.sync()

        profile = system.get_conceptual_parent()  # TODO: null-check
        distro = profile.get_conceptual_parent()  # TODO: null-check
        blend_data = utils.blender(self.api, False, system)

        system_config = self._gen_system_config(system, blend_data, distro)
        self.config = utils.merge_dicts_recursive(
            self.config,
            {"date": time.asctime(time.gmtime()), "dhcp_tags": system_config},
        )
        self._write_configs(self.config)
        return self.restart_service()

    def sync(self):
        """
        Generate full config from scratch, write it to the filesystem, and restart DHCP service.
        """
        # Reset generic counter and cached config
        self.generic_entry_cnt = 0
        self.config = self.gen_full_config()
        self._write_configs(self.config)
        return self.restart_service()

    def _gen_system_config(
        self,
        system_obj: "System",
        system_blend_data: Dict[str, Any],
        distro_obj: Optional["Distro"],
    ):
        """
        Generate DHCP config for a single system.

        :param system_obj: System to generate DHCP config for
        :param system_blend_data: utils.blender() data for the System
        :param distro_object: Optional, is used to access distro-specific information like arch when present
        """
        dhcp_tags = {"default": {}}
        processed_system_master_interfaces = set()
        ignore_macs = set()
        if not system_obj.is_management_supported(cidr_ok=False):
            self.logger.debug(
                "%s does not meet precondition: MAC, IPv4, or IPv6 address is required.",
                system_obj.name,
            )
            return {}

        for iface_name, iface_obj in system_obj.interfaces.items():
            # TODO: move to own function
            iface = iface_obj.to_dict()
            mac = iface_obj.mac_address
            if (
                not self.settings.always_write_dhcp_entries
                and not system_blend_data["netboot_enabled"]
                and iface["static"]
            ):
                continue
            if not mac:
                self.logger.warning("%s has no MAC address", system_obj.name)
                continue

            iface["gateway"] = iface_obj.if_gateway or system_obj.gateway
            if iface["interface_type"] in (
                "bond_slave",
                "bridge_slave",
                "bonded_bridge_slave",
            ):
                if iface["interface_master"] not in system_obj.interfaces:
                    # Can't write DHCP entry: master interface does not exist
                    continue

                master_name = iface["interface_master"]
                master_iface = system_obj.interfaces[master_name]
                # There may be multiple bonded interfaces, need composite index
                system_master_name = f"{system_obj.name}-{master_name}"
                if system_master_name not in processed_system_master_interfaces:
                    processed_system_master_interfaces.add(system_master_name)
                else:
                    ignore_macs.add(mac)
                # IPv4
                iface["netmask"] = master_iface.netmask
                iface["ip_address"] = master_iface.ip_address
                if not iface["ip_address"]:
                    iface["ip_address"] = self._find_ip_addr(
                        system_obj.interfaces, prefix=master_name, ip_version="ipv4"
                    )
                # IPv6
                iface["ipv6_address"] = master_iface.ipv6_address
                if not iface["ipv6_address"]:
                    iface["ipv6_address"] = self._find_ip_addr(
                        system_obj.interfaces, prefix=master_name, ip_version="ipv6"
                    )
                # common
                host = master_iface.dns_name
                dhcp_tag = master_iface.dhcp_tag
            else:
                # TODO: simplify _slave / non_slave branches
                host = iface["dns_name"]
                dhcp_tag = iface["dhcp_tag"]

            if distro_obj is not None:
                iface["distro"] = distro_obj.to_dict()
            if host:
                if iface_name == "eth0":
                    iface["name"] = host
                else:
                    iface["name"] = f"{host}-{iface_name}"
            else:
                self.generic_entry_cnt += 1
                iface["name"] = f"generic{self.generic_entry_cnt:d}"

            for key in (
                "next_server_v6",
                "next_server_v4",
                "filename",
                "netboot_enabled",
                "hostname",
                "enable_ipxe",
                "name_servers",
            ):  # TODO: what if they don't exist? original code has .get("filename"), but shouldn't be needed?
                iface[key] = system_blend_data[key]
            iface["owner"] = system_blend_data["name"]
            # esxi
            if distro_obj is not None and distro_obj.os_version.startswith("esxi"):
                iface["filename_esxi"] = (
                    "esxi/system",
                    # config filename can be None
                    system_obj.get_config_filename(interface=iface_name, loader="pxe")
                    or "",
                    "mboot.efi",
                )
            elif distro_obj is not None and not iface["filename"]:
                if distro_obj.arch in (
                    Archs.PPC,
                    Archs.PPC64,
                    Archs.PPC64LE,
                    Archs.PPC64EL,
                ):
                    iface["filename"] = "grub/grub.ppc64le"
                elif distro_obj.arch == Archs.AARCH64:
                    iface["filename"] = "grub/grubaa64.efi"

            if not dhcp_tag:
                dhcp_tag = system_blend_data.get("dhcp_tag", "default")
            if dhcp_tag not in dhcp_tags:
                dhcp_tags[dhcp_tag] = {mac: iface}
            else:
                dhcp_tags[dhcp_tag][mac] = iface

        for macs in dhcp_tags.values():
            for mac in list(macs.keys()):
                if mac in ignore_macs:
                    del macs[mac]

        return dhcp_tags

    def _find_ip_addr(
        self,
        interfaces: Dict[str, "NetworkInterface"],
        prefix: str,
        ip_version: str,
    ) -> str:
        """Find the first interface with an IP address that begins with prefix."""

        if ip_version.lower() == "ipv4":
            attr_name = "ip_address"
        elif ip_version.lower() == "ipv6":
            attr_name = "ipv6_address"
        else:
            return ""

        for name, obj in interfaces:
            if name.startswith(prefix + ".") and hasattr(obj, attr_name):
                return getattr(obj, attr_name)
        return ""

    def gen_full_config(self):
        """Generate DHCP configuration for all systems."""
        dhcp_tags = {"default": {}}
        self.generic_entry_cnt = 0
        for system in self.systems:
            profile = system.get_conceptual_parent()
            if profile is None:
                continue
            distro: Optional[Distro] = profile.get_conceptual_parent()  # type: ignore
            blended_system = utils.blender(self.api, False, system)
            new_tags = self._gen_system_config(system, blended_system, distro)
            dhcp_tags = utils.merge_dicts_recursive(dhcp_tags, new_tags)

        metadata = {
            "date": time.asctime(time.gmtime()),
            "cobbler_server": f"{self.settings.server}:{self.settings.http_port}",
            "next_server_v4": self.settings.next_server_v4,
            "next_server_v6": self.settings.next_server_v6,
            "dhcp_tags": dhcp_tags,
        }
        return metadata

    def _write_config(
        self,
        config_data,
        template_file,
        settings_file,
    ) -> None:
        """DHCP files are written when ``manage_dhcp_v4`` or ``manage_dhcp_v6``
        is set in the settings for the respective version. DHCPv4 files are
        written when ``manage_dhcp_v4`` is set in our settings.

        :param config_data:
        :param template_file: The location of the DHCP template.
        """
        try:
            with open(template_file, "r", encoding="UTF-8") as template_fd:
                template_data = template_fd.read()
        except OSError as e:
            self.logger.error("Can't read dhcp template '%s':\n%s", template_file, e)
            return None
        config_copy = config_data.copy() # template rendering changes the passed dict
        self.logger.info("Writing %s", settings_file)
        self.templar.render(template_data, config_copy, settings_file)

    def write_v4_config(
        self, config_data=None, template_file="/etc/cobbler/dhcp.template"
    ):
        if not config_data:
            raise ValueError("No config to write.")
        self._write_config(config_data, template_file, self.settings_file_v4)

    def write_v6_config(
        self, config_data=None, template_file="/etc/cobbler/dhcp6.template"
    ):
        if not config_data:
            raise ValueError("No config to write.")
        self._write_config(config_data, template_file, self.settings_file_v6)

    def _write_configs(self, config_data=None) -> None:
        if not config_data:
            raise ValueError("No config to write.")

        if self.settings.manage_dhcp_v4:
            self.write_v4_config(config_data)
        if self.settings.manage_dhcp_v6:
            self.write_v6_config(config_data)

    def write_configs(self) -> None:
        data = self.gen_full_config()
        self._write_configs(data)

    def restart_dhcp(self, service_name: str) -> int:
        """
        This syncs the dhcp server with it's new config files.
        Basically this restarts the service to apply the changes.

        :param service_name: The name of the DHCP service.
        """
        dhcpd_path = shutil.which(service_name)
        return_code_service_restart = utils.subprocess_call([dhcpd_path, "-t", "-q"], shell=False)
        if return_code_service_restart != 0:
            self.logger.error("Testing config - {} -t failed".format(service_name))
        return_code_service_restart = utils.service_restart(service_name)
        if return_code_service_restart != 0:
            self.logger.error("{} service failed".format(service_name))
        return return_code_service_restart

    def restart_service(self) -> int:
        if not self.settings.restart_dhcp:
            return 0

        # Even if one fails, try both and return an error
        ret = 0
        if self.settings.manage_dhcp_v4:
            service_v4 = utils.dhcp_service_name()
            ret |= self.restart_dhcp(service_v4)
        if self.settings.manage_dhcp_v6:
            # TODO: Fix hard coded string
            ret |= self.restart_dhcp("dhcpd6")
        return ret


def get_manager(api):
    """
    Creates a manager object to manage an isc dhcp server.

    :param api: The API which holds all information in the current Cobbler instance.
    :return: The object to manage the server with.
    """
    # Singleton used, therefore ignoring 'global'
    global MANAGER  # pylint: disable=global-statement

    if not MANAGER:
        MANAGER = _IscManager(api)
    return MANAGER
