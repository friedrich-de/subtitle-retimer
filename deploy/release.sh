#!/usr/bin/env bash
# Builds a release of the service on the server and makes it the current one.
# Run as the deploying account, never as root:
#
#   /opt/retimer/current/deploy/release.sh          the head of main
#   /opt/retimer/current/deploy/release.sh REF      a branch, tag or commit
#
# A release is a directory of its own under /opt/retimer/releases, named by its
# commit, with the code and the Python environment it was tested with. The
# tests run before anything changes, and going back is a matter of pointing
# `current` at the release before:
#
#   ln -sfn releases/COMMIT /opt/retimer/current.new
#   mv -T /opt/retimer/current.new /opt/retimer/current
#
# The running service keeps the release it started with until it is restarted:
#
#   sudo systemctl restart retimer
set -euo pipefail

ROOT=${RETIMER_ROOT:-/opt/retimer}
REF=${1:-main}
PYTHON=3.13
KEEP=3

if [ "$(id -u)" -eq 0 ]; then
    echo "error: run this as the deploying account, not as root" >&2
    exit 1
fi

# Everything uv needs stays under the root, where the service account can read it.
export UV_PYTHON_INSTALL_DIR=$ROOT/python
export UV_CACHE_DIR=$ROOT/cache
export UV_PYTHON_PREFERENCE=only-managed
export UV_NO_PROGRESS=1
# A public repository is read over HTTPS. A private one (a fork, say) is read
# over SSH with a deploy key of its own, kept at .ssh/deploy_key.
if [ -e "$ROOT/.ssh/deploy_key" ]; then
    REPOSITORY=${RETIMER_REPOSITORY:-git@github.com:friedrich-de/subtitle-retimer.git}
    export GIT_SSH_COMMAND="ssh -i $ROOT/.ssh/deploy_key -o IdentitiesOnly=yes -o BatchMode=yes \
-o UserKnownHostsFile=$ROOT/.ssh/known_hosts -o StrictHostKeyChecking=yes"
else
    REPOSITORY=${RETIMER_REPOSITORY:-https://github.com/friedrich-de/subtitle-retimer.git}
fi
umask 022

cd "$ROOT"
if [ ! -d repo.git ]; then
    git clone --quiet --bare "$REPOSITORY" repo.git
fi
git --git-dir repo.git fetch --quiet --prune origin '+refs/heads/*:refs/heads/*' \
    '+refs/tags/*:refs/tags/*'
commit=$(git --git-dir repo.git rev-parse --verify --quiet "$REF^{commit}") || {
    echo "error: the repository has no $REF" >&2
    exit 1
}
release=$ROOT/releases/$commit

if [ ! -e "$release/.ready" ]; then
    echo "building $commit"
    rm -rf "$release"
    mkdir -p "$release"
    trap 'rm -rf "$release"' ERR
    git --git-dir repo.git archive "$commit" | tar -x -C "$release"
    (cd "$release" && "$ROOT/bin/uv" sync --quiet --locked --extra dev --python "$PYTHON")
    echo "testing $commit"
    (cd "$release" && .venv/bin/pytest -q -p no:cacheprovider)
    touch "$release/.ready"
    trap - ERR
fi

ln -sfn "releases/$commit" "$ROOT/current.new"
mv -T "$ROOT/current.new" "$ROOT/current"

# Old releases go, the newest few stay to go back to.
ls -1dt "$ROOT"/releases/*/ | tail -n "+$((KEEP + 1))" | while read -r old; do
    old=${old%/}
    if [ "$old" != "$release" ]; then
        rm -rf "$old"
    fi
done

echo "release $commit is current"
if ! cmp -s "$release/deploy/retimer.service" /etc/systemd/system/retimer.service ||
    ! cmp -s "$release/deploy/retimer_secrets.py" /usr/local/sbin/retimer-secrets; then
    echo "the unit or the secrets tool changed. Install them first:"
    echo "  sudo bash $ROOT/current/deploy/server-setup.sh"
fi
echo "restart the service to run it:  sudo systemctl restart retimer"
