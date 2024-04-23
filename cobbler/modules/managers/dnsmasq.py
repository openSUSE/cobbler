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
from cobbler import utils

import time

import cobbler.utils as utils
from cobbler.manager import ManagerModule

MANAGER = None

COBBLER_HOSTS_FILE = "/var/lib/cobbler/cobbler_hosts"
ETHERS_FILE = "/etc/ethers"


def register() -> str:
    """
    The mandatory Cobbler modules registration hook.

    :return: Always "manage".
    """
    return "manage"


class _DnsmasqManager(ManagerModule):
    """
    Handles conversion of internal state to the tftpboot tree layout.
    """

    @staticmethod
    def what() -> str:
        """
        This identifies the module.

        :return: Will always return ``dnsmasq``.
        """
        return "dnsmasq"

    def sync(self):
        """
        Generate full config from scratch, write it to the filesystem, and restart DHCP service.
        """
        # Reset cached config
        self.config = self.gen_full_config()
        self._write_configs(self.config)
        return self.restart_service()

    def write_configs(self) -> None:
        """
        DHCP files are written when ``manage_dhcp`` is set in our settings.

        :raises OSError
        :raises ValueError
        """
        data = self.gen_full_config()
        self._write_configs(data)

    def _write_configs(self, config_data=None) -> None:
        """
        Internal function to write DHCP files.

        :raises OSError
        :raises ValueError
        """
        if not config_data:
            raise ValueError("No config to write.")

        settings_file = "/etc/dnsmasq.conf"
        template_file = "/etc/cobbler/dnsmasq.template"

        try:
            f2 = open(template_file, "r")
        except Exception:
            raise OSError("error writing template to file: %s" % template_file)
        template_data = f2.read()
        f2.close()

        config_copy = config_data.copy() # template rendering changes the passed dict
        self.logger.info("Writing %s", settings_file)
        self.templar.render(template_data, config_copy, settings_file)

    def gen_full_config(self):
        """Generate DHCP configuration for all systems."""
        system_definitions: Dict[str, str] = {}

        for system in self.systems:
            profile = system.get_conceptual_parent()
            if profile is None:
                continue
            distro: Distro = profile.get_conceptual_parent()  # type: ignore
            if distro is None:
                continue
            system_config = self._gen_system_config(system)
            system_definitions = utils.merge_dicts_recursive(system_definitions, system_config, str_append=True)

        metadata = {
            "insert_cobbler_system_definitions": system_definitions.get("default", ""),
            "date": time.asctime(time.gmtime()),
            "cobbler_server": self.settings.server,
            "next_server_v4": self.settings.next_server_v4,
            "next_server_v6": self.settings.next_server_v6,
        }

        # now add in other DHCP expansions that are not tagged with "default"
        for x in list(system_definitions.keys()):
            if x == "default":
                continue
            metadata["insert_cobbler_system_definitions_%s" % x] = system_definitions[x]

        return metadata

    def _gen_system_config(
        self,
        system_obj: "System",
    ):
        """
        Generate dnsmasq config for a single system.

        :param system_obj: System to generate dnsmasq config for
        """
        # we used to just loop through each system, but now we must loop
        # through each network interface of each system.
        system_definitions: Dict[str, str] = {}

        if not system_obj.is_management_supported(cidr_ok=False):
            self.logger.debug(
                "%s does not meet precondition: MAC, IPv4, or IPv6 address is required.",
                system_obj.name,
            )
            return {}

        profile = system_obj.get_conceptual_parent()
        if profile is None:
            raise ValueError("Profile for system not found!")
        distro = profile.get_conceptual_parent()
        if distro is None:
            raise ValueError("Distro for system not found!")

        for interface in system_obj.interfaces.values():
            mac = interface.mac_address
            ip_address = interface.ip_address
            host = interface.dns_name
            ipv6 = interface.ipv6_address

            if not mac:
                # can't write a DHCP entry for this system
                continue

            # In many reallife situations there is a need to control the IP address and hostname for a specific
            # client when only the MAC address is available. In addition to that in some scenarios there is a need
            # to explicitly label a host with the applicable architecture in order to correctly handle situations
            # where we need something other than ``pxelinux.0``. So we always write a dhcp-host entry with as much
            # info as possible to allow maximum control and flexibility within the dnsmasq config.

            systxt = "dhcp-host=net:" + distro.arch.value.lower() + "," + mac

            if host != "":
                systxt += "," + host

            if ip_address != "":
                systxt += "," + ip_address
            if ipv6 != "":
                systxt += f",[{ipv6}]"

            systxt += "\n"

            dhcp_tag = interface.dhcp_tag
            if dhcp_tag == "":
                dhcp_tag = "default"

            if dhcp_tag not in system_definitions:
                system_definitions[dhcp_tag] = ""

            system_definitions[dhcp_tag] = system_definitions[dhcp_tag] + systxt

        return system_definitions

    def sync_single_system(self, system: "System"):
        if not self.config:
            # cache miss, need full sync for consistent data
            return self.sync()

        system_config = self._gen_system_config(system)
        self.config = utils.merge_dicts_recursive(
            self.config,
            {"date": time.asctime(time.gmtime()), "system_definitions": system_config},
            str_append=True,
        )
        self._write_configs(self.config)
        return self.restart_service()

    def regen_ethers(self):
        """
        This function regenerates the ethers file. To get more information please read ``man ethers``, the format is
        also in there described.
        """
        with open(ETHERS_FILE, "w", encoding="UTF-8") as ethers_fh:
            for system in self.systems:
                ethers_entry = self._gen_single_ethers_entry(system)
                if ethers_entry:
                    ethers_fh.write(ethers_entry)

    def _gen_single_ethers_entry(
        self,
        system_obj: "System",
    ):
        if not system_obj.is_management_supported(cidr_ok=False):
            self.logger.debug(
                "%s does not meet precondition: MAC, IPv4, or IPv6 address is required.",
                system_obj.name,
            )
            return

        output = ''
        for interface in system_obj.interfaces.values():
            mac = interface.mac_address
            ip_address = interface.ip_address
            if not mac:
                # can't write this w/o a MAC address
                continue
            if ip_address != "":
                output += mac.upper() + "\t" + ip_address + "\n"
        return output

    def sync_single_ethers_entry(
        self,
        system_obj: "System",
    ):
        """
        This adds a new single system entry to the ethers file.
        """
        # dnsmasq knows how to read this database of MACs -> IPs, so we'll keep it up to date every time we add a
        # system.
        with open(ETHERS_FILE, "a", encoding="UTF-8") as ethers_fh:
            ethers_entry = self._gen_single_ethers_entry(system)
            if host_entry:
                ethers_fh.write(ethers_entry)

    def regen_hosts(self) -> None:
        """
        This rewrites the hosts file and thus also rewrites the dns config.
        """
        # dnsmasq knows how to read this database for host info (other things may also make use of this later)
        with open(COBBLER_HOSTS_FILE, "w", encoding="UTF-8") as regen_hosts_fd:
            for system in self.systems:
                host_entry = self._gen_single_host_entry(system)
                if host_entry:
                    regen_hosts_fd.write(host_entry)

    def _gen_single_host_entry(
        self,
        system_obj: "System",
    ):
        if not system_obj.is_management_supported(cidr_ok=False):
            self.logger.debug(
                "%s does not meet precondition: MAC, IPv4, or IPv6 address is required.",
                system_obj.name,
            )
            return

        output = ''
        for (_, interface) in system_obj.interfaces.items():
            mac = interface.mac_address
            host = interface.dns_name
            ipv4 = interface.ip_address
            ipv6 = interface.ipv6_address
            if not mac:
                continue
            if host != "" and ipv6 != "":
                output += ipv6 + "\t" + host + "\n"
            elif host != "" and ipv4 != "":
                output += ipv4 + "\t" + host + "\n"
        return output

    def sync_single_hosts_entry(
        self,
        system_obj: "System",
    ):
        """
        This adds a single system entry to the hosts file
        """
        with open(COBBLER_HOSTS_FILE, "a", encoding="UTF-8") as regen_hosts_fd:
            host_entry = self._gen_single_host_entry(system_obj)
            if host_entry:
                regen_hosts_fd.write(entry_host)

    def restart_service(self) -> int:
        """
        This restarts the dhcp server and thus applied the newly written config files.
        """
        service_name = "dnsmasq"
        if self.settings.restart_dhcp:
            return_code_service_restart = utils.process_management.service_restart(
                service_name
            )
            if return_code_service_restart != 0:
                self.logger.error("%s service failed", service_name)
            return return_code_service_restart


def get_manager(api):
    """
    Creates a manager object to manage a dnsmasq server.

    :param api: The API to resolve all information with.
    :return: The object generated from the class.
    """
    # Singleton used, therefore ignoring 'global'
    global MANAGER  # pylint: disable=global-statement

    if not MANAGER:
        MANAGER = _DnsmasqManager(api)
    return MANAGER
