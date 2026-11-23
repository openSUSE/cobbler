from unittest.mock import MagicMock
import pytest

from cobbler.modules.managers import in_tftpd
from cobbler.tftpgen import TFTPGen
from cobbler.items.system import System


@pytest.fixture(scope="function", autouse=True)
def reset_singleton():
    in_tftpd.MANAGER = None
    yield
    in_tftpd.MANAGER = None


def test_write_all_system_files_default_menu_items_none(mocker):
    """
    Verify that write_all_system_files works when menu_items is omitted (defaults to None),
    and does not inject empty menu items into metadata.
    """
    mock_api = MagicMock()
    mock_api.settings().tftpboot_location = "/srv/tftpboot"
    generator = TFTPGen(mock_api)

    mock_system = MagicMock(spec=System)
    mock_system.name = "test-system"
    mock_system.arch = "x86_64"
    mock_system.interfaces = {"eth0": MagicMock()}
    mock_system.is_management_supported.return_value = True
    mock_system.get_config_filename.side_effect = ["pxe_cfg", "grub_cfg"]

    mock_profile = MagicMock()
    mock_distro = MagicMock()
    from cobbler.enums import Archs
    mock_distro.arch = Archs.X86_64
    mock_profile.get_conceptual_parent.return_value = mock_distro
    mock_system.get_conceptual_parent.return_value = mock_profile

    mock_write_pxe = mocker.patch.object(generator, "write_pxe_file")
    mocker.patch("cobbler.utils.mkdir")
    mocker.patch("cobbler.utils.rmfile")
    mocker.patch("os.symlink")

    # Call with default menu_items=None
    generator.write_all_system_files(mock_system)

    # Verify write_pxe_file was called
    assert mock_write_pxe.call_count >= 1
    # Check that metadata passed to write_pxe_file does not contain 'menu_items'
    for call in mock_write_pxe.call_args_list:
        kwargs = call[1]
        if "metadata" in kwargs:
            assert "menu_items" not in kwargs["metadata"]


def test_sync_single_system_does_not_call_get_menu_items(mocker):
    """
    Verify that sync_single_system does NOT invoke get_menu_items().
    """
    mock_api = MagicMock()
    manager = in_tftpd.get_manager(mock_api)
    mock_tftpgen = MagicMock(spec=TFTPGen)
    mocker.patch.object(manager, "tftpgen", mock_tftpgen)

    mock_system = MagicMock(spec=System)
    manager.sync_single_system(mock_system)

    # get_menu_items MUST NOT be called
    assert mock_tftpgen.get_menu_items.call_count == 0
    # system boot files and templates MUST be written
    mock_tftpgen.write_all_system_files.assert_called_once_with(mock_system, None)
    mock_tftpgen.write_templates.assert_called_once_with(mock_system)


def test_sync_systems_does_not_call_get_menu_items_redundantly(mocker):
    """
    Verify that sync_systems delegates menu generation to make_pxe_menu and
    does NOT call get_menu_items before looping systems.
    """
    mock_api = MagicMock()
    manager = in_tftpd.get_manager(mock_api)
    mock_tftpgen = MagicMock(spec=TFTPGen)
    mocker.patch.object(manager, "tftpgen", mock_tftpgen)
    mock_sync_single = mocker.patch.object(manager, "sync_single_system")

    mock_system1 = MagicMock(spec=System)
    mock_system2 = MagicMock(spec=System)
    mock_api.find_system.side_effect = [mock_system1, mock_system2]

    manager.sync_systems(["sys1", "sys2"], verbose=True)

    # get_menu_items MUST NOT be called in sync_systems
    assert mock_tftpgen.get_menu_items.call_count == 0
    # Each system should be synced
    assert mock_sync_single.call_count == 2
    # make_pxe_menu called once
    mock_tftpgen.make_pxe_menu.assert_called_once()


def test_sync_does_not_call_get_menu_items_before_system_writes(mocker):
    """
    Verify that full sync does not call get_menu_items redundantly before
    writing system files.
    """
    mock_api = MagicMock()
    manager = in_tftpd.get_manager(mock_api)
    mock_tftpgen = MagicMock(spec=TFTPGen)
    mocker.patch.object(manager, "tftpgen", mock_tftpgen)

    mock_system = MagicMock(spec=System)
    mock_api.systems.return_value = [mock_system]
    mock_api.distros.return_value = []

    manager.sync()

    # get_menu_items MUST NOT be called in sync
    assert mock_tftpgen.get_menu_items.call_count == 0
    # make_pxe_menu called once to generate top-level menus
    mock_tftpgen.make_pxe_menu.assert_called_once()
