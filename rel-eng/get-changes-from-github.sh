#!/bin/bash
#
# This script fetch sources from SOURCE_GIT_REPO at SOURCE_BRANCH branch
# and extract them in the 'cobbler' directory.
#
# It also takes care of updating the spec file and changelog file
#
# Then, it produces a commit and push the changes to TARGET_REPO
# at TARGET_BRANCH branch.
#
# IMPORTANT: This script MUST be called from the repository root:
# 	     ./rel-eng/get-changes-from-github.sh
#
# NOTE: This script is used by manager-Head-releng-cobbler-to-gitea job
#       in our internal Jenkins
#

########### Configuration
SOURCE_GIT_REPO="https://github.com/openSUSE/cobbler"
SOURCE_BRANCH="mlm/head"
TARGET_REPO="https://src.suse.de/Galaxy/cobbler"
TARGET_BRANCH="mlm-5.2"
COMMIT_AUTHOR="Jenkins: Cobbler to Gitea Automation <salt-ci@suse.de>"
##########

set -x

TEMP_REPO_NAME="cobbler_gitea"
ARCHIVE_URL="$SOURCE_GIT_REPO/archive/refs/heads/$SOURCE_BRANCH.tar.gz"
CURRENT_PWD="$PWD"

# Remove any previous cached TEMP_REPO_NAME
rm $TEMP_REPO_NAME -rf || true

# Clone target repository
git clone -q $TARGET_REPO $TEMP_REPO_NAME
cd $TEMP_REPO_NAME

# Select target branch (mlm-5.2)
git checkout -q $TARGET_BRANCH

# Get source changes and prepare package
echo "Fetching code from $ARCHIVE_URL ..."
curl -sL $ARCHIVE_URL -o cobbler.tar.gz

echo "Extracting files under 'cobbler' directory ..."
tar xf cobbler.tar.gz
rsync -q -avz cobbler-*/ cobbler

echo "Update spec file and changelog according to Github sources ..."
cp cobbler/cobbler.spec cobbler.spec
cp cobbler/cobbler.changes cobbler.changes

# Remove unneeded files after arranging content
rm cobbler.tar.gz
rm cobbler-* -rf

# Commit all changes and push to branch if changes are detected
echo "Checking for changes to sync ..."
if git diff --quiet; then
    echo "--> No new changes to sync"
else
    echo "--> New changes are detected. Syncing ..."
    git commit -a -m "Sync changes from https://github.com/openSUSE/cobbler (branch mlm/head)" --author "$COMMIT_AUTHOR" --no-gpg-sign
    git push -q origin $TARGET_BRANCH
fi

# Clean the environment
echo "Cleaning environment ..."
cd ..
rm $TEMP_REPO_NAME -rf

echo "Done!"
