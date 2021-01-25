"""
Copyright 2021, SUSE LLC
Thomas Renninger <trenn@suse.de>
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

import cobbler.utils as utils
from cobbler.manager import ManagerModule
import logging


def register():
    """
    The mandatory Cobbler module registration hook.
    """
    return "manage"


class _IPSync(ManagerModule):

    @staticmethod
    def what():
        """
        Static method to identify the manager.

        :return: Always "isc".
        """
        return "ip_sync"

    # ToDo: Get rid of logger
    def __init__(self, collection_mgr, logger):
        super().__init__(collection_mgr, logger)

    def set_interface_ips(self, intf, set_v4, set_v6):
        fqdn = intf.get("dns_name")
        try:
            ip_v4, ip_v6 = utils.get_ips_from_dns(fqdn)
            if set_v4:
                if ip_v4:
                    logging.info("Fetched IPv4 [{}] for {}".format(ip_v4, fqdn))
                    intf["ip_address"] = ip_v4
                else:
                    logging.warning("Could not fetch IPv4 for {}".format(fqdn))
            if set_v6:
                if ip_v6:
                    logging.info("Fetched IPv6 [{}] for {}".format(ip_v6, fqdn))
                    intf["ipv6_address"] = ip_v6
                else:
                    logging.warning("Could not fetch IPv6 for {}".format(fqdn))
        except Exception as e:
            logging.exception("Could not fetch IPs for {} - {}".format(
                intf.get("dns_name"), repr(e)))

    def write_configs(self):
        intf_names = ("default", "bmc")

        for system in self.systems:
            for intf_name in intf_names:
                intf = system.interfaces.get(intf_name)
                if not intf:
                    continue
                fqdn = intf.get("dns_name")
                if intf.get("static") or not fqdn:
                    continue

                dhcpv4 = str(self.settings.enable_dhcpv4).lower()
                dhcpv6 = str(self.settings.enable_dhcpv6).lower()

                fetch_v4 = False
                fetch_v6 = False
                if dhcpv4 != "0":
                    ip_v4 = intf.get("ip_address")
                    if not ip_v4:
                        fetch_v4 = True
                if dhcpv6 != "0":
                    ip_v6 = intf.get("ipv6_address")
                    if not ip_v6:
                        fetch_v6 = True
                self.set_interface_ips(intf, fetch_v4, fetch_v6)


manager = None


def get_manager(collection_mgr, logger):
    """
    Creates a manager object to manage an isc dhcp server.

    :param collection_mgr: The collection manager which holds all information
    :param logger: The logger to audit all actions with.
    :return: The object to manage the server with.
    """
    global manager

    if not manager:
        manager = _IPSync(collection_mgr, logger)
    return manager
