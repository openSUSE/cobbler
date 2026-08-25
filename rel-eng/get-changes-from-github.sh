#!/bin/bash
#
# Script to sync changes from GitHub to Gitea via PR
#
# Description:
#   Automates the synchronization of a source Git repository (e.g., GitHub)
#   to one or more target Gitea branches. Unlike a direct mirror, this script
#   restructures the source files into a specific subdirectory on the target,
#   extracts package building files, and manages the Pull Request lifecycle.
#
# Core workflow (repeated for every branch in TARGET_BRANCHES):
#   1. CLONE & CHECK: Fetches the latest source HEAD (once) and checks if the
#      target base branch or its dedicated sync branch already has this commit.
#   2. RESTRUCTURE: Copies source files into a target PACKAGE_NAME subdirectory
#      (ignoring '.git' directory) and extract 'PACKAGE_NAME.spec' and
#      'PACKAGE_NAME.changes' files.
#   3. COMMIT & PUSH: Creates a new commit mapping to the upstream hash
#      and force-pushes to a dedicated sync branch on Gitea if there are changes.
#   4. PR MANAGEMENT: Uses the Gitea API to ensure exactly one open PR exists
#      for that base branch. It creates a new PR if none exists, or updates
#      the existing open PR to reflect the newly synced commit.
#
# Dependencies:
#   - git, rsync, curl, jq
#
# Required Environment Variables:
#   - GITEA_TOKEN: A valid access token for the target Gitea instance.
#
# Usage:
#   ./get-changes-from-github.sh             # Run standard sync
#   ./get-changes-from-github.sh --dry-run   # Run without pushing or making API calls
#
# NOTE: This script is used by manager-uyuni-releng-cobbler-to-gitea job
#       in our internal Jenkins
#
########### Configuration
SOURCE_GIT_REPO="https://github.com/openSUSE/cobbler"
SOURCE_BRANCH="mlm/head"
TARGET_REPO="https://src.suse.de/Galaxy/cobbler"
# One or more base branches to sync to. Each one gets its own dedicated
# sync branch (named "${TARGET_SYNC_BRANCH_PREFIX}-<target-branch>") and its
# own Pull Request, since the restructured content is committed on top of
# each base branch individually.
TARGET_BRANCHES=("devel_mlm-main" "devel_mlm-5.2")
TARGET_SYNC_BRANCH_PREFIX="auto-sync-mlm-head"
COMMIT_AUTHOR_NAME="Jenkins: Cobbler to Gitea Automation"
COMMIT_AUTHOR_EMAIL="salt-ci@suse.de"
PACKAGE_NAME="cobbler"
##########

set +x
set -e

NO_PROTO="${TARGET_REPO#https://}"
IFS='/' read -r GITEA_DOMAIN GITEA_ORG GITEA_REPO <<< "$NO_PROTO"

log_info()    { echo -e "[INFO] $1"; }
log_success() { echo -e "[SUCCESS] $1"; }
log_warn()    { echo -e "[WARN] $1"; }
log_error()   { echo -e "[ERROR] $1"; exit 1; }

DRY_RUN=false
if [[ "$1" == "--dry-run" ]]; then
    DRY_RUN=true
    log_warn "Running in DRY-RUN mode. No changes will be pushed."
fi

if [[ -z "$GITEA_TOKEN" ]]; then
    log_error "GITEA_TOKEN environment variable is not set."
fi

for cmd in git curl jq rsync; do
    if ! command -v $cmd &> /dev/null; then
        log_error "$cmd could not be found. Please install it."
    fi
done

TARGET_REMOTE_URL="https://${GITEA_TOKEN}@${NO_PROTO}"

# Prepare workspace
TMP_DIR=$(mktemp -d)
log_info "Created temporary workspace at $TMP_DIR"
trap 'rm -rf "$TMP_DIR"; log_info "Cleaned up workspace $TMP_DIR"' EXIT

# Clone source and identify HEAD
log_info "Cloning source repository..."
git clone -q -b "$SOURCE_BRANCH" --depth 1 "$SOURCE_GIT_REPO" "$TMP_DIR/source_repo"
cd "$TMP_DIR/source_repo"
SOURCE_SHA=$(git rev-parse HEAD)
log_info "Source HEAD commit hash: $SOURCE_SHA"

# Clone target repository once. Not passing -b so that all remote branches
# are fetched and can be checked out individually for each target branch below.
log_info "Cloning target repository..."
git clone -q "$TARGET_REMOTE_URL" "$TMP_DIR/target_repo"
cd "$TMP_DIR/target_repo"

API_BASE="https://${GITEA_DOMAIN}/api/v1/repos/${GITEA_ORG}/${GITEA_REPO}"
AUTH_HEADER="Authorization: token ${GITEA_TOKEN}"

COMMIT_TITLE="Automatic sync from source branch $SOURCE_BRANCH"
COMMIT_BODY="Source-Repo: $SOURCE_GIT_REPO
Source-Branch: $SOURCE_BRANCH
Source-Commit: $SOURCE_SHA"

RESULT_LINES=()

# Syncs $SOURCE_SHA to a single target base branch: restructures files,
# commits/pushes to a dedicated sync branch, and manages its PR.
sync_target_branch() {
    local TARGET_BRANCH="$1"
    local TARGET_SYNC_BRANCH="${TARGET_SYNC_BRANCH_PREFIX}-${TARGET_BRANCH}"

    echo ""
    log_info "===== Target branch: $TARGET_BRANCH (sync branch: $TARGET_SYNC_BRANCH) ====="

    git checkout -q -B "$TARGET_BRANCH" "origin/$TARGET_BRANCH"

    # Check if the target base branch already contains our source commit
    if [[ -n $(git log -1 --grep="Source-Commit: $SOURCE_SHA") ]]; then
        log_success "Target branch '$TARGET_BRANCH' already contains commit $SOURCE_SHA. Nothing to do."
        RESULT_LINES+=("  - $TARGET_BRANCH: already up to date, nothing to do")
        return 0
    fi

    local NEEDS_PUSH=true

    # Check if the sync branch exists remotely and already contains our source commit
    if git ls-remote --exit-code --heads origin "$TARGET_SYNC_BRANCH" >/dev/null 2>&1; then
        log_info "Found existing remote sync branch '$TARGET_SYNC_BRANCH'. Checking its commits..."
        git fetch -q origin "$TARGET_SYNC_BRANCH"

        if [[ -n $(git log -1 --grep="Source-Commit: $SOURCE_SHA" FETCH_HEAD) ]]; then
            log_info "Remote sync branch is already up to date with $SOURCE_SHA. Skipping push, proceeding to PR check."
            NEEDS_PUSH=false
        fi
    fi

    # Restructure, commit, and push (only if needed)
    if [[ "$NEEDS_PUSH" == true ]]; then
        # Create or reset our dedicated sync branch to the latest target base branch
        git checkout -q -B "$TARGET_SYNC_BRANCH"

        log_info "Restructuring files from source to target..."
        mkdir -p "$PACKAGE_NAME"

        # Sync sources (excluding git metadata)
        rsync -a --checksum --delete --exclude='.git' "$TMP_DIR/source_repo/" "$PACKAGE_NAME/"

        # Extract the .spec and .changes files
        for ext in spec changes; do
            if [[ -f "$PACKAGE_NAME/${PACKAGE_NAME}.${ext}" ]]; then
                cp "$PACKAGE_NAME/${PACKAGE_NAME}.${ext}" ./
                log_info "Extracted ${PACKAGE_NAME}.${ext}"
            fi
        done

        git add -A

        if git diff --staged --quiet; then
            log_warn "No file differences detected for '$TARGET_BRANCH' despite new commit hash. Aborting sync for this branch."
            RESULT_LINES+=("  - $TARGET_BRANCH: no file differences detected, skipped")
            return 0
        fi

        log_info "Committing changes..."

        if [[ "$DRY_RUN" == true ]]; then
            log_warn "[DRY-RUN] Would have committed as '$COMMIT_AUTHOR_NAME <$COMMIT_AUTHOR_EMAIL>'"
            log_warn "[DRY-RUN] Would have force-pushed to Gitea branch: $TARGET_SYNC_BRANCH"
        else
            git config user.name "$COMMIT_AUTHOR_NAME"
            git config user.email "$COMMIT_AUTHOR_EMAIL"

            git commit -q --no-gpg-sign -m "$COMMIT_TITLE" -m "$COMMIT_BODY"
            log_info "Pushing to Gitea branch '$TARGET_SYNC_BRANCH'..."
            git push -q --force origin "$TARGET_SYNC_BRANCH"
            log_success "Successfully pushed restructured files to Gitea."
        fi
    else
        log_info "Bypassed local file restructuring and push phase."
    fi

    # Check and create or update existing PR for this base branch
    log_info "Checking for existing Pull Request..."
    # Only look for open pull requests
    local PR_RESPONSE
    PR_RESPONSE=$(curl -s -H "$AUTH_HEADER" "${API_BASE}/pulls?state=open")

    # Extract the PR number and existing body if an open PR exists
    local PR_NUMBER
    PR_NUMBER=$(echo "$PR_RESPONSE" | jq -e --arg head "$TARGET_SYNC_BRANCH" --arg base "$TARGET_BRANCH" \
        '.[] | select(.head.ref == $head and .base.ref == $base) | .number' 2>/dev/null || echo "false")

    local PR_BODY_TEXT="This is an automated pull request to sync upstream changes from ${SOURCE_GIT_REPO} (branch \`${SOURCE_BRANCH}\`).

Synced up to commit: \`${SOURCE_SHA}\`"

    local RESULT_STATUS=""

    if [[ "$PR_NUMBER" != "false" ]]; then

        # Extract current body from the API response
        local PR_BODY_CURRENT
        PR_BODY_CURRENT=$(echo "$PR_RESPONSE" | jq -r --arg head "$TARGET_SYNC_BRANCH" --arg base "$TARGET_BRANCH" \
            '.[] | select(.head.ref == $head and .base.ref == $base) | .body // empty' 2>/dev/null)

        # Strip carriage returns to ensure safe string comparison
        local CLEAN_CURRENT_BODY CLEAN_NEW_BODY
        CLEAN_CURRENT_BODY=$(echo "$PR_BODY_CURRENT" | tr -d '\r')
        CLEAN_NEW_BODY=$(echo "$PR_BODY_TEXT" | tr -d '\r')

        if [[ "$CLEAN_CURRENT_BODY" == "$CLEAN_NEW_BODY" ]]; then
            log_success "Open PR already exists (#$PR_NUMBER) and the description is already up to date. No update needed."
            RESULT_STATUS="PR #$PR_NUMBER already up to date"
        else
            log_info "Open PR exists (#$PR_NUMBER), but the description is outdated. Updating PR body..."

            local JSON_PAYLOAD
            JSON_PAYLOAD=$(jq -n --arg body "$PR_BODY_TEXT" '{body: $body}')

            if [[ "$DRY_RUN" == true ]]; then
                log_warn "[DRY-RUN] Would have updated existing PR #$PR_NUMBER body via Gitea API."
                RESULT_STATUS="[DRY-RUN] would update PR #$PR_NUMBER"
            else
                local UPDATE_RESPONSE UPDATED_PR_URL
                UPDATE_RESPONSE=$(curl -s -X PATCH -H "$AUTH_HEADER" \
                    -H "Content-Type: application/json" \
                    -d "$JSON_PAYLOAD" \
                    "${API_BASE}/pulls/${PR_NUMBER}")

                UPDATED_PR_URL=$(echo "$UPDATE_RESPONSE" | jq -r '.html_url // empty')

                if [[ -n "$UPDATED_PR_URL" ]]; then
                    log_success "Pull Request body successfully updated!"
                    log_success "PR URL: $UPDATED_PR_URL"
                    RESULT_STATUS="PR #$PR_NUMBER updated: $UPDATED_PR_URL"
                else
                    log_error "Failed to update PR. Gitea API response:\n$UPDATE_RESPONSE"
                fi
            fi
        fi
    else
        log_info "No open PR found for this sync branch. Creating a new Pull Request..."

        local JSON_PAYLOAD
        JSON_PAYLOAD=$(jq -n \
            --arg title "$COMMIT_TITLE" \
            --arg head "$TARGET_SYNC_BRANCH" \
            --arg base "$TARGET_BRANCH" \
            --arg body "$PR_BODY_TEXT" \
            '{title: $title, head: $head, base: $base, body: $body}')

        if [[ "$DRY_RUN" == true ]]; then
            log_warn "[DRY-RUN] Would have created a new PR via Gitea API."
            RESULT_STATUS="[DRY-RUN] would create new PR"
        else
            local CREATE_RESPONSE NEW_PR_URL
            CREATE_RESPONSE=$(curl -s -X POST -H "$AUTH_HEADER" \
                -H "Content-Type: application/json" \
                -d "$JSON_PAYLOAD" \
                "${API_BASE}/pulls")

            NEW_PR_URL=$(echo "$CREATE_RESPONSE" | jq -r '.html_url // empty')

            if [[ -n "$NEW_PR_URL" ]]; then
                log_success "Pull Request successfully created!"
                log_success "PR URL: $NEW_PR_URL"
                RESULT_STATUS="PR created: $NEW_PR_URL"
            else
                log_error "Failed to create PR. Gitea API response:\n$CREATE_RESPONSE"
            fi
        fi
    fi

    RESULT_LINES+=("  - $TARGET_BRANCH: $RESULT_STATUS")
}

for branch in "${TARGET_BRANCHES[@]}"; do
    sync_target_branch "$branch"
done

echo ""
echo "==========================================================="
echo -e "Sync Execution Summary"
echo "==========================================================="
echo -e "Source Repo:    ${SOURCE_GIT_REPO} (${SOURCE_BRANCH})"
echo -e "Synced Commit:  ${SOURCE_SHA}"
echo -e "Target Repo:    ${TARGET_REPO}"
echo -e "Subdirectory:   /${PACKAGE_NAME}/"
echo -e "Target Branches:"
for line in "${RESULT_LINES[@]}"; do
    echo -e "$line"
done
if [[ "$DRY_RUN" == true ]]; then
    echo -e "Status:         Dry Run Completed Successfully"
else
    echo -e "Status:         Sync Completed Successfully"
fi
echo "==========================================================="
echo
