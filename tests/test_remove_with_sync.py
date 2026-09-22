from unittest.mock import MagicMock, call
import pytest

from cobbler.api import CobblerAPI
from cobbler.remote import CobblerXMLRPCInterface
from cobbler.cobbler_collections.profiles import Profiles
from cobbler.cobbler_collections.distros import Distros
from cobbler.cobbler_collections.images import Images
from cobbler.cobbler_collections.menus import Menus
from cobbler.items.profile import Profile
from cobbler.items.distro import Distro
from cobbler.items.image import Image
from cobbler.items.menu import Menu


class TestApiRemoveWithSync:
    """Tests for with_sync parameter in CobblerAPI remove_* methods."""

    @pytest.mark.parametrize("with_sync", [True, False])
    def test_api_remove_item_forwards_with_sync(self, with_sync):
        api = MagicMock(spec=CobblerAPI)
        mock_collection = MagicMock()
        api.get_items.return_value = mock_collection
        mock_item = MagicMock()
        mock_item.name = "test_item"
        api.get_item.return_value = mock_item

        CobblerAPI.remove_item(
            api,
            what="system",
            ref="test_item",
            recursive=False,
            delete=True,
            with_triggers=True,
            with_sync=with_sync,
        )

        mock_collection.remove.assert_called_once_with(
            "test_item",
            recursive=False,
            with_delete=True,
            with_triggers=True,
            with_sync=with_sync,
        )

    @pytest.mark.parametrize(
        "method_name,what",
        [
            ("remove_distro", "distro"),
            ("remove_profile", "profile"),
            ("remove_system", "system"),
            ("remove_repo", "repo"),
            ("remove_image", "image"),
            ("remove_mgmtclass", "mgmtclass"),
            ("remove_package", "package"),
            ("remove_file", "file"),
            ("remove_menu", "menu"),
        ],
    )
    def test_api_specific_remove_forwards_with_sync(self, method_name, what):
        api = MagicMock(spec=CobblerAPI)
        method = getattr(CobblerAPI, method_name)

        # Call with with_sync=False
        method(api, "item1", recursive=True, delete=False, with_triggers=False, with_sync=False)

        api.remove_item.assert_called_once_with(
            what,
            "item1",
            recursive=True,
            delete=False,
            with_triggers=False,
            with_sync=False,
        )


class TestRemoteRemoveWithSync:
    """Tests for with_sync parameter in CobblerXMLRPCInterface remove_* methods."""

    @pytest.mark.parametrize("with_sync", [True, False])
    def test_remote_remove_item_forwards_with_sync(self, with_sync):
        remote = MagicMock(spec=CobblerXMLRPCInterface)
        remote.transactions = {}
        remote.api = MagicMock()
        mock_item = MagicMock()
        remote.api.get_item.return_value = mock_item

        CobblerXMLRPCInterface.remove_item(
            remote,
            what="profile",
            name="test_prof",
            token="valid_token",
            recursive=True,
            with_sync=with_sync,
        )

        remote.api.remove_item.assert_called_once_with(
            "profile",
            "test_prof",
            delete=True,
            with_triggers=True,
            recursive=True,
            with_sync=with_sync,
        )

    @pytest.mark.parametrize(
        "method_name,what",
        [
            ("remove_distro", "distro"),
            ("remove_profile", "profile"),
            ("remove_system", "system"),
            ("remove_repo", "repo"),
            ("remove_image", "image"),
            ("remove_mgmtclass", "mgmtclass"),
            ("remove_package", "package"),
            ("remove_file", "file"),
            ("remove_menu", "menu"),
        ],
    )
    def test_remote_specific_remove_forwards_with_sync(self, method_name, what):
        remote = MagicMock(spec=CobblerXMLRPCInterface)
        method = getattr(CobblerXMLRPCInterface, method_name)

        method(remote, "item1", "valid_token", recursive=False, with_sync=False)

        remote.remove_item.assert_called_once_with(
            what,
            "item1",
            "valid_token",
            False,
            with_sync=False,
        )


class TestCollectionRecursiveConsolidatedSync:
    """Tests that recursive removals pass with_sync=False to children and perform consolidated sync."""

    def test_profile_recursive_remove_consolidates_sync(self, mocker):
        mock_api = MagicMock()
        mock_collection_mgr = MagicMock()
        mock_collection_mgr.api = mock_api
        profiles_coll = Profiles(mock_collection_mgr)

        # Create parent profile and child descendants
        parent = MagicMock(spec=Profile)
        parent.name = "parent_profile"
        parent.depth = 1

        child_sys = MagicMock()
        child_sys.name = "child_system"
        child_sys.depth = 2
        child_sys.COLLECTION_TYPE = "system"

        child_sub = MagicMock()
        child_sub.name = "child_subprofile"
        child_sub.depth = 2
        child_sub.COLLECTION_TYPE = "profile"

        parent.descendants = [child_sys, child_sub]

        profiles_coll.listing = {"parent_profile": parent}
        mocker.patch.object(profiles_coll, "find", return_value=parent)
        mocker.patch("cobbler.utils.run_triggers")

        mock_sync = MagicMock()
        mock_api.get_sync.return_value = mock_sync

        # Execute recursive removal with with_sync=True
        profiles_coll.remove("parent_profile", recursive=True, with_sync=True)

        # Verify child removals were called with with_sync=False
        remove_calls = mock_api.remove_item.call_args_list
        assert len(remove_calls) == 2
        for call_item in remove_calls:
            assert call_item[1].get("with_sync") is False

        # Verify single consolidated sync was called at the end
        mock_sync.sync.assert_called_once_with(verbose=False)
        assert mock_sync.remove_single_profile.call_count == 0

    def test_profile_recursive_remove_with_sync_false_skips_all_syncs(self, mocker):
        mock_api = MagicMock()
        mock_collection_mgr = MagicMock()
        mock_collection_mgr.api = mock_api
        profiles_coll = Profiles(mock_collection_mgr)

        parent = MagicMock(spec=Profile)
        parent.name = "parent_profile"
        parent.depth = 1

        child = MagicMock()
        child.name = "child_system"
        child.depth = 2
        child.COLLECTION_TYPE = "system"
        parent.descendants = [child]

        profiles_coll.listing = {"parent_profile": parent}
        mocker.patch.object(profiles_coll, "find", return_value=parent)
        mocker.patch("cobbler.utils.run_triggers")

        mock_sync = MagicMock()
        mock_api.get_sync.return_value = mock_sync

        # Execute recursive removal with with_sync=False
        profiles_coll.remove("parent_profile", recursive=True, with_sync=False)

        # Children removed with with_sync=False
        assert mock_api.remove_item.call_args[1].get("with_sync") is False
        # No sync called at all
        assert mock_sync.sync.call_count == 0
        assert mock_sync.remove_single_profile.call_count == 0

    def test_distro_recursive_remove_consolidates_sync(self, mocker):
        mock_api = MagicMock()
        mock_collection_mgr = MagicMock()
        mock_collection_mgr.api = mock_api
        mock_api.settings().webdir = "/srv/www/cobbler"
        distros_coll = Distros(mock_collection_mgr)

        distro = MagicMock(spec=Distro)
        distro.name = "test_distro"
        distro.kernel = "/var/lib/cobbler/distros/test_kernel"

        child_prof = MagicMock()
        child_prof.name = "child_prof"

        distros_coll.listing = {"test_distro": distro}
        mocker.patch.object(distros_coll, "find", return_value=distro)
        mock_api.find_items.return_value = [child_prof]
        mocker.patch("cobbler.utils.run_triggers")

        mock_sync = MagicMock()
        mock_api.get_sync.return_value = mock_sync

        # Execute recursive removal with with_sync=True
        distros_coll.remove("test_distro", recursive=True, with_sync=True)

        # Verify remove_profile was called with with_sync=False
        mock_api.remove_profile.assert_called_once_with(
            child_prof,
            recursive=True,
            delete=True,
            with_triggers=True,
            with_sync=False,
        )

        # Verify single consolidated sync was called at the end
        mock_sync.sync.assert_called_once_with(verbose=False)

    def test_distro_recursive_remove_with_sync_false_skips_all_syncs(self, mocker):
        mock_api = MagicMock()
        mock_collection_mgr = MagicMock()
        mock_collection_mgr.api = mock_api
        mock_api.settings().webdir = "/srv/www/cobbler"
        distros_coll = Distros(mock_collection_mgr)

        distro = MagicMock(spec=Distro)
        distro.name = "test_distro"
        distro.kernel = "/var/lib/cobbler/distros/test_kernel"

        child_prof = MagicMock()
        child_prof.name = "child_prof"

        distros_coll.listing = {"test_distro": distro}
        mocker.patch.object(distros_coll, "find", return_value=distro)
        mock_api.find_items.return_value = [child_prof]
        mocker.patch("cobbler.utils.run_triggers")

        mock_sync = MagicMock()
        mock_api.get_sync.return_value = mock_sync

        # Execute with with_sync=False
        distros_coll.remove("test_distro", recursive=True, with_sync=False)

        # Verify remove_profile called with with_sync=False
        assert mock_api.remove_profile.call_args[1].get("with_sync") is False
        # No sync called
        assert mock_sync.sync.call_count == 0

    def test_image_recursive_remove_consolidates_sync(self, mocker):
        mock_api = MagicMock()
        mock_collection_mgr = MagicMock()
        mock_collection_mgr.api = mock_api
        images_coll = Images(mock_collection_mgr)

        image = MagicMock(spec=Image)
        image.name = "test_image"

        child_sys = MagicMock()
        child_sys.name = "child_sys"

        images_coll.listing = {"test_image": image}
        mocker.patch.object(images_coll, "find", return_value=image)
        mock_api.find_items.return_value = [child_sys]
        mocker.patch("cobbler.utils.run_triggers")

        mock_sync = MagicMock()
        mock_api.get_sync.return_value = mock_sync

        images_coll.remove("test_image", recursive=True, with_sync=True)

        mock_api.remove_system.assert_called_once_with(
            child_sys,
            recursive=True,
            with_sync=False,
        )
        mock_sync.sync.assert_called_once_with(verbose=False)

    def test_menu_recursive_remove_consolidates_sync(self, mocker):
        mock_api = MagicMock()
        mock_collection_mgr = MagicMock()
        mock_collection_mgr.api = mock_api
        menus_coll = Menus(mock_collection_mgr)

        menu = MagicMock(spec=Menu)
        menu.name = "test_menu"
        menu.depth = 1

        child_sub = MagicMock()
        child_sub.name = "child_submenu"
        child_sub.depth = 2
        child_sub.COLLECTION_TYPE = "menu"
        menu.descendants = [child_sub]

        menus_coll.listing = {"test_menu": menu}
        mocker.patch.object(menus_coll, "find", return_value=menu)
        mock_api.find_items.return_value = []
        mocker.patch("cobbler.utils.run_triggers")

        mock_sync = MagicMock()
        mock_api.get_sync.return_value = mock_sync

        menus_coll.remove("test_menu", recursive=True, with_sync=True)

        mock_api.remove_item.assert_called_once_with(
            "menu",
            child_sub,
            recursive=False,
            delete=True,
            with_triggers=True,
            with_sync=False,
        )
        mock_sync.sync.assert_called_once_with(verbose=False)
