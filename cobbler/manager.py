import cobbler.templar as templar
import cobbler.utils as utils


class ManagerModule(object):

    @staticmethod
    def what():
        """
        Static method to identify the manager module.
        Must be overwritten by the inheriting class
        """
        return "undefined"

    # ToDo: Get rid of logger
    def __init__(self, collection_mgr, logger):
        """
        Constructor

        :param collection_mgr: The collection manager to resolve all information with.
        :param logger: The logger to audit all actions with.
        """
        self.logger = logger
        self.collection_mgr = collection_mgr
        self.api = collection_mgr.api
        self.distros = collection_mgr.distros()
        self.profiles = collection_mgr.profiles()
        self.systems = collection_mgr.systems()
        self.settings = collection_mgr.settings()
        self.repos = collection_mgr.repos()
        self.templar = templar.Templar(collection_mgr)

    def write_configs(self):
        """
        Write module specific config files.
        E.g. dhcp manager would write /etc/dhcpd.conf here
        """
        pass

    def restart_service(self):
        """
        Write module specific config files.
        E.g. dhcp manager would write /etc/dhcpd.conf here
        """
        pass

    def regen_ethers(self):
        """
        ISC/BIND doesn't use this. It is there for compability reasons with other managers.
        """
        pass

    def sync(self, verbose=False):
        """
        This syncs the manager's server (systemd service) with it's new config files.
        Basically this restarts the service to apply the changes.

        :return: Integer return value of restart_service - 0 on success 
        """
        self.write_configs()
        return self.restart_service()

