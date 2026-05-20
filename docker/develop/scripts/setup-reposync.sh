#!/bin/bash

# Dependencies for reposync tests
zypper install --no-recommends -y dnf python3-librepo python3-bcrypt dnf dnf-plugins-core wget \
       perl-LockFile-Simple perl-LWP-Protocol-https ed

curl -L -O https://download.fedoraproject.org/pub/fedora/linux/releases/44/Everything/x86_64/os/Packages/d/debmirror-2.47-4.fc44.noarch.rpm
dnf install -y debmirror-2.47-4.fc44.noarch.rpm
