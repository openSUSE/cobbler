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

import time
import copy

import cobbler.utils as utils
from cobbler.manager import ManagerModule

from cobbler.cexceptions import CX
from cobbler.utils import _


def register():
    """
    The mandatory Cobbler module registration hook.
    """
    return "manage"


class _IscManager(ManagerModule):

    @staticmethod
    def what():
        """
        Static method to identify the manager.

        :return: Always "isc".
        """
        return "isc"

    # ToDo: Get rid of logger
    def __init__(self, collection_mgr, logger):
        super().__init__(collection_mgr, logger)

        self.settings_file = utils.dhcpconf_location(self.api)
        self.settings_file_v6 = '/etc/dhcpd6.conf'

    def write_v4_config(self):
        """
        DHCP files are written when ``manage_dhcp`` is set in ``/etc/cobbler/settings``.
        """

        template_file = "/etc/cobbler/dhcp.template"
        blender_cache = {}

        try:
            f2 = open(template_file, "r")
        except Exception:
            raise CX(_("error reading template: %s") % template_file)
        template_data = ""
        template_data = f2.read()
        f2.close()

        # Use a simple counter for generating generic names where a hostname is not available.
        counter = 0

        # We used to just loop through each system, but now we must loop through each network interface of each system.
        dhcp_tags = {"default": {}}
        yaboot = "/yaboot"

        # FIXME: ding should evolve into the new dhcp_tags dict
        ding = {}
        ignore_macs = []

        for system in self.systems:
            if not system.is_management_supported(cidr_ok=False):
                continue

            profile = system.get_conceptual_parent()
            distro = profile.get_conceptual_parent()

            # if distro is None then the profile is really an image record
            for (name, system_interface) in list(system.interfaces.items()):

                # We make a copy because we may modify it before adding it to the dhcp_tags and we don't want to affect
                # the master copy.
                interface = copy.deepcopy(system_interface)

                if interface["if_gateway"]:
                    interface["gateway"] = interface["if_gateway"]
                else:
                    interface["gateway"] = system.gateway

                mac = interface["mac_address"]

                if interface["interface_type"] in ("bond_slave", "bridge_slave", "bonded_bridge_slave"):

                    if interface["interface_master"] not in system.interfaces:
                        # Can't write DHCP entry; master interface does not exist
                        continue

                    # We may have multiple bonded interfaces, so we need a composite index into ding.
                    name_master = "%s-%s" % (system.name, interface["interface_master"])
                    if name_master not in ding:
                        ding[name_master] = {interface["interface_master"]: []}

                    if len(ding[name_master][interface["interface_master"]]) == 0:
                        ding[name_master][interface["interface_master"]].append(mac)
                    else:
                        ignore_macs.append(mac)

                    ip = system.interfaces[interface["interface_master"]]["ip_address"]
                    netmask = system.interfaces[interface["interface_master"]]["netmask"]
                    dhcp_tag = system.interfaces[interface["interface_master"]]["dhcp_tag"]
                    host = system.interfaces[interface["interface_master"]]["dns_name"]

                    if ip is None or ip == "":
                        for (nam2, int2) in list(system.interfaces.items()):
                            if (nam2.startswith(interface["interface_master"] + ".") and int2["ip_address"] is not None and int2["ip_address"] != ""):
                                ip = int2["ip_address"]
                                break

                    interface["ip_address"] = ip
                    interface["netmask"] = netmask
                else:
                    ip = interface["ip_address"]
                    netmask = interface["netmask"]
                    dhcp_tag = interface["dhcp_tag"]
                    host = interface["dns_name"]

                if distro is not None:
                    interface["distro"] = distro.to_dict()

                if mac is None or mac == "":
                    # can't write a DHCP entry for this system
                    continue

                counter = counter + 1

                # the label the entry after the hostname if possible
                if host is not None and host != "":
                    if name != "eth0":
                        interface["name"] = "%s-%s" % (host, name)
                    else:
                        interface["name"] = "%s" % (host)
                else:
                    interface["name"] = "generic%d" % counter

                # add references to the system, profile, and distro
                # for use in the template
                if system.name in blender_cache:
                    blended_system = blender_cache[system.name]
                else:
                    blended_system = utils.blender(self.api, False, system)
                    blender_cache[system.name] = blended_system

                interface["next_server"] = blended_system["next_server"]
                interface["filename"] = blended_system.get("filename")
                interface["netboot_enabled"] = blended_system["netboot_enabled"]
                interface["hostname"] = blended_system["hostname"]
                interface["owner"] = blended_system["name"]
                interface["enable_gpxe"] = blended_system["enable_gpxe"]
                interface["name_servers"] = blended_system["name_servers"]
                interface["mgmt_parameters"] = blended_system["mgmt_parameters"]

                # Explicitly declare filename for other (non x86) archs as in DHCP discover package mostly the
                # architecture cannot be differed due to missing bits...
                if distro is not None and not interface.get("filename"):
                    if distro.arch == "ppc" or distro.arch == "ppc64":
                        interface["filename"] = yaboot
                    elif distro.arch == "ppc64le":
                        interface["filename"] = "grub/grub.ppc64le"
                    elif distro.arch == "aarch64":
                        interface["filename"] = "grub/grubaa64.efi"

                if not self.settings.always_write_dhcp_entries:
                    if not interface["netboot_enabled"] and interface['static']:
                        continue

                if dhcp_tag == "":
                    dhcp_tag = blended_system.get("dhcp_tag", "")
                    if dhcp_tag == "":
                        dhcp_tag = "default"

                if dhcp_tag not in dhcp_tags:
                    dhcp_tags[dhcp_tag] = {
                        mac: interface
                    }
                else:
                    dhcp_tags[dhcp_tag][mac] = interface

        # Remove macs from redundant slave interfaces from dhcp_tags otherwise you get duplicate ip's in the installer.
        for dt in list(dhcp_tags.keys()):
            for m in list(dhcp_tags[dt].keys()):
                if m in ignore_macs:
                    del dhcp_tags[dt][m]

        # we are now done with the looping through each interface of each system
        metadata = {
            "date": time.asctime(time.gmtime()),
            "cobbler_server": "%s:%s" % (self.settings.server, self.settings.http_port),
            "next_server": self.settings.next_server,
            "yaboot": yaboot,
            "dhcp_tags": dhcp_tags
        }
        if self.logger is not None:
            self.logger.info("generating %s" % self.settings_file)
        self.templar.render(template_data, metadata, self.settings_file, None)

    def write_v6_config(self):
        """
        DHCP IPv6 files are written when ``manage_dhcp6`` is set in ``/etc/cobbler/settings``.
        """

        template_file = "/etc/cobbler/dhcp6.template"
        blender_cache = {}

        try:
            f2 = open(template_file, "r")
        except Exception:
            raise CX(_("error reading template: %s") % template_file)
        template_data = ""
        template_data = f2.read()
        f2.close()

        # Use a simple counter for generating generic names where a hostname is not available.
        counter = 0

        # We used to just loop through each system, but now we must loop through each network interface of each system.
        dhcp_tags = {"default": {}}

        # FIXME: ding should evolve into the new dhcp_tags dict
        ding = {}
        ignore_macs = []

        for system in self.systems:
            if not system.is_management_supported(cidr_ok=False):
                continue

            profile = system.get_conceptual_parent()
            distro = profile.get_conceptual_parent()

            # if distro is None then the profile is really an image record
            for (name, system_interface) in list(system.interfaces.items()):

                # We make a copy because we may modify it before adding it to the dhcp_tags and we don't want to affect
                # the master copy.
                interface = copy.deepcopy(system_interface)

                if interface["if_gateway"]:
                    interface["gateway"] = interface["if_gateway"]
                else:
                    interface["gateway"] = system.gateway

                mac = interface["mac_address"]

                if interface["interface_type"] in ("bond_slave", "bridge_slave", "bonded_bridge_slave"):

                    if interface["interface_master"] not in system.interfaces:
                        # Can't write DHCP entry; master interface does not exist
                        continue

                    # We may have multiple bonded interfaces, so we need a composite index into ding.
                    name_master = "%s-%s" % (system.name, interface["interface_master"])
                    if name_master not in ding:
                        ding[name_master] = {interface["interface_master"]: []}

                    if len(ding[name_master][interface["interface_master"]]) == 0:
                        ding[name_master][interface["interface_master"]].append(mac)
                    else:
                        ignore_macs.append(mac)

                    ip_v6 = system.interfaces[interface["interface_master"]]["ipv6_address"]
                    dhcp_tag = system.interfaces[interface["interface_master"]]["dhcp_tag"]
                    host = system.interfaces[interface["interface_master"]]["dns_name"]

                    if ip_v6 is None or ip_v6 == "":
                        for (nam2, int2) in list(system.interfaces.items()):
                            if (nam2.startswith(interface["interface_master"] + ".") and \
                                int2["ipv6_address"] is not None and int2["ipv6_address"] != ""):
                                ip = int2["ipv6_address"]
                                break

                    interface["ipv6_address"] = ip_v6
                else:
                    ip_v6 = interface["ipv6_address"]
                    dhcp_tag = interface["dhcp_tag"]
                    host = interface["dns_name"]

                if distro is not None:
                    interface["distro"] = distro.to_dict()

                if mac is None or mac == "":
                    # can't write a DHCP entry for this system
                    continue

                counter = counter + 1

                # the label the entry after the hostname if possible
                if host is not None and host != "":
                    if name != "eth0":
                        interface["name"] = "%s-%s" % (host, name)
                    else:
                        interface["name"] = "%s" % (host)
                else:
                    interface["name"] = "generic%d" % counter

                # add references to the system, profile, and distro
                # for use in the template
                if system.name in blender_cache:
                    blended_system = blender_cache[system.name]
                else:
                    blended_system = utils.blender(self.api, False, system)
                    blender_cache[system.name] = blended_system

                interface["filename"] = blended_system.get("filename")
                interface["netboot_enabled"] = blended_system["netboot_enabled"]
                interface["hostname"] = blended_system["hostname"]
                interface["owner"] = blended_system["name"]
                interface["name_servers"] = blended_system["name_servers"]
                interface["mgmt_parameters"] = blended_system["mgmt_parameters"]

                # Explicitly declare filename for other (non x86) archs as in DHCP discover package mostly the
                # architecture cannot be differed due to missing bits...
                if distro is not None and not interface.get("filename"):
                    if distro.arch == "ppc":
                        interface["filename"] = "grub/grub.ppc"
                    elif distro.arch == "ppc64":
                        interface["filename"] = "grub/grub.ppc64"
                    elif distro.arch == "ppc64le":
                        interface["filename"] = "grub/grub.ppc64le"
                    elif distro.arch == "aarch64":
                        interface["filename"] = "grub/grubaa64.efi"

                if not self.settings.always_write_dhcp_entries:
                    if not interface["netboot_enabled"] and interface['static']:
                        continue

                if dhcp_tag == "":
                    dhcp_tag = blended_system.get("dhcp_tag", "")
                    if dhcp_tag == "":
                        dhcp_tag = "default"

                if dhcp_tag not in dhcp_tags:
                    dhcp_tags[dhcp_tag] = {
                        mac: interface
                    }
                else:
                    dhcp_tags[dhcp_tag][mac] = interface

        # Remove macs from redundant slave interfaces from dhcp_tags otherwise you get duplicate ip's in the installer.
        for dt in list(dhcp_tags.keys()):
            for m in list(dhcp_tags[dt].keys()):
                if m in ignore_macs:
                    del dhcp_tags[dt][m]

        # we are now done with the looping through each interface of each system
        metadata = {
            "date": time.asctime(time.gmtime()),
            "next_server_v6": self.settings.next_server,
            "dhcp_tags": dhcp_tags
        }

        if self.logger is not None:
            self.logger.info("generating %s" % self.settings_file_v6)
        self.templar.render(template_data, metadata, self.settings_file_v6, None)


    def restart_dhcp(self, service_name):
        """
        This syncs the dhcp server with it's new config files.
        Basically this restarts the service to apply the changes.
        """
        rc = 0
        rc = utils.subprocess_call(self.logger, "%s -t -q".format(service_name), shell=True)
        if rc != 0:
            self.logger.error("%s -t failed".format(service_name))
            service_restart = "service %s restart".format(service_name)
            rc = utils.subprocess_call(self.logger, service_restart, shell=True)
            if rc != 0:
                self.logger.error("%s service failed".format(service_name))
        return rc


    def write_configs(self):
        dhcpv4 = str(self.settings.enable_dhcpv4).lower()
        dhcpv6 = str(self.settings.enable_dhcpv6).lower()

        if dhcpv4 != "0":
            self.write_v4_config()
        if dhcpv6 != "0":
            self.write_v6_config()

    def restart_service(self):
        restart_dhcp = str(self.settings.restart_dhcp).lower()
        if restart_dhcp == "0":
            return 0

        service_v4 = utils.dhcp_service_name(self.api)

        dhcpv4 = str(self.settings.enable_dhcpv4).lower()
        dhcpv6 = str(self.settings.enable_dhcpv6).lower()

        # Even if one fails, try both and return an error
        ret = 0
        if dhcpv4 != "0":
            ret |= self.restart_dhcp(service_v4)
        if dhcpv6 != "0":
            ret |= self.restart_dhcp("dhcpv6")
        return ret


manager = None

def get_manager(collection_mgr, logger):
    """
    Creates a manager object to manage an isc dhcp server.

    :param collection_mgr: The collection manager which holds all information in the current Cobbler instance.
    :param logger: The logger to audit all actions with.
    :return: The object to manage the server with.
    """
    global manager

    if not manager:
        manager = _IscManager(collection_mgr, logger)
    return manager
