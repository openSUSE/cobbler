"""
This test module tries to automatically replicate all security incidents we had in the past and checks if they fail.
"""
# SPDX-License-Identifier: GPL-2.0-or-later
import base64
import crypt
import logging
import os
import subprocess
import xmlrpc.client

import pytest

from cobbler.api import CobblerAPI
from cobbler.modules.authentication import pam


# ==================== START ysf ====================

# SPDX-FileCopyrightText: 2022 ysf <nicolas.chatelain@tnpconsultants.com>


def test_pam_login_with_expired_user():
    # Arrange
    test_api = CobblerAPI()
    test_username = "expired_user"
    test_password = "password"
    # create pam testuser
    subprocess.run(["useradd", "-p", crypt.crypt(test_password), test_username])
    # change user to be expired
    subprocess.run(["chage", "-E0", test_username])

    # Act - Try login
    result = pam.authenticate(test_api, test_username, test_password)

    # Assert - Login failed
    assert not result

# ==================== END ysf ====================
