#!/bin/bash
#
# This script fetch sources from SOURCE_GIT_REPO at SOURCE_BRANCH branch
# and extract them in the 'cobbler' directory.
#
# It also takes care of updating the spec file
#
# IMPORTANT: This script MUST be called from the repository root:
# 	     ./rel-eng/get-changes-from-github.sh
#

set -e

SOURCE_GIT_REPO="https://github.com/openSUSE/cobbler"
SOURCE_BRANCH="mlm/head"
ARCHIVE_URL="$SOURCE_GIT_REPO/archive/refs/heads/$SOURCE_BRANCH.tar.gz"

echo "Fetching code from $ARCHIVE_URL ..."
curl -sL $ARCHIVE_URL -o cobbler.tar.gz

echo "Extracting files under 'cobbler' directory ..."
tar xf cobbler.tar.gz 
rsync -q -avz cobbler-*/ cobbler

echo "Update spec file according to Github sources"
cp cobbler/*spec cobbler.spec

echo "Cleaning files ..."
rm cobbler.tar.gz
rm cobbler-* -rf

echo "Done! Don't forget to upgrade your changelog file!!"
