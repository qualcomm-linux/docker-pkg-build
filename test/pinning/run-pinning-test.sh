#!/usr/bin/env bash
#
# Regression test for --extra-repo-priority (qualcomm-linux/docker-pkg-build#48).
#
# Publishes two conflicting versions of a synthetic libqcomdummy-dev package
# via two separate flat APT repos (served by two throwaway nginx containers
# on the docker default bridge network, so each gets its own IP with no
# manual network setup), then builds test/pinning/package (which
# Build-Depends on libqcomdummy-dev and fails unless the "qcom" version won
# dependency resolution):
#
#   1. Without --extra-repo-priority: expect the build to FAIL, because APT
#      picks the higher-numbered "upstream" version - this proves the bug
#      from #48 is real and reproducible.
#   2. With --extra-repo-priority pinning the "qcom" repo ahead of the
#      "upstream" one: expect the build to SUCCEED - this proves the fix.
#
# The two repos must be reachable at genuinely different hostnames/IPs, not
# just different ports on one host: APT's `Pin: origin "<host>"` matches by
# hostname only and ignores the port, so same-host-different-port repos
# can't be told apart by the pin mechanism --extra-repo-priority actually
# uses (confirmed empirically during development of this test). Using two
# separate containers sidesteps this for free, since each gets its own
# Docker-assigned IP on the bridge network.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PIN_TEST_PACKAGE="${SCRIPT_DIR}/package"

UPSTREAM_VERSION="1.0-2"
QCOM_VERSION="1.0-1+qcom1"
REPO_UPSTREAM_CONTAINER="pin-test-repo-upstream-$$"
REPO_QCOM_CONTAINER="pin-test-repo-qcom-$$"

WORK_DIR="$(mktemp -d)"

cleanup() {
  docker stop "$REPO_UPSTREAM_CONTAINER" "$REPO_QCOM_CONTAINER" >/dev/null 2>&1 || true
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

echo "== Installing equivs (used to fabricate synthetic .deb packages) =="
sudo apt-get update -qq
sudo apt-get install -y -qq equivs dpkg-dev

build_equivs_deb() {
  local version="$1" out_dir="$2"
  local ctrl="${WORK_DIR}/equivs-control-${version}"
  cat > "$ctrl" <<EOF
Package: libqcomdummy-dev
Version: ${version}
Maintainer: Qualcomm Linux CI <noreply@qualcomm.com>
Architecture: all
Description: Synthetic dummy library for docker-pkg-build CI pin-priority test
 Fabricated via equivs; used only to exercise APT version/pin resolution
 for the --extra-repo-priority regression test.
EOF
  (cd "$out_dir" && equivs-build "$ctrl" >"${WORK_DIR}/equivs-build-${version}.log" 2>&1) \
    || { cat "${WORK_DIR}/equivs-build-${version}.log"; exit 1; }
}

echo "== Fabricating two conflicting libqcomdummy-dev versions =="
REPO_UPSTREAM_DIR="${WORK_DIR}/repo-upstream"
REPO_QCOM_DIR="${WORK_DIR}/repo-qcom"
mkdir -p "$REPO_UPSTREAM_DIR" "$REPO_QCOM_DIR"

build_equivs_deb "$UPSTREAM_VERSION" "$REPO_UPSTREAM_DIR"
build_equivs_deb "$QCOM_VERSION" "$REPO_QCOM_DIR"

(cd "$REPO_UPSTREAM_DIR" && dpkg-scanpackages . /dev/null > Packages 2>/dev/null)
(cd "$REPO_QCOM_DIR" && dpkg-scanpackages . /dev/null > Packages 2>/dev/null)

echo "== Serving each repo from its own throwaway container (own IP on the default bridge network) =="
docker run -d --rm --name "$REPO_UPSTREAM_CONTAINER" \
  -v "${REPO_UPSTREAM_DIR}:/usr/share/nginx/html:ro" nginx:alpine >/dev/null
docker run -d --rm --name "$REPO_QCOM_CONTAINER" \
  -v "${REPO_QCOM_DIR}:/usr/share/nginx/html:ro" nginx:alpine >/dev/null
sleep 1

UPSTREAM_IP="$(docker inspect -f '{{.NetworkSettings.Networks.bridge.IPAddress}}' "$REPO_UPSTREAM_CONTAINER")"
QCOM_IP="$(docker inspect -f '{{.NetworkSettings.Networks.bridge.IPAddress}}' "$REPO_QCOM_CONTAINER")"
echo "repo-upstream at ${UPSTREAM_IP}, repo-qcom at ${QCOM_IP}"

UPSTREAM_REPO_LINE="deb [trusted=yes] http://${UPSTREAM_IP}/ ./"
QCOM_REPO_LINE="deb [trusted=yes] http://${QCOM_IP}/ ./"

echo "== Self-check: confirming both repos are reachable the same way sbuild will reach them =="
for ip in "$UPSTREAM_IP" "$QCOM_IP"; do
  if ! docker run --rm curlimages/curl:latest -sf --max-time 5 "http://${ip}/Packages" >/dev/null; then
    echo "ERROR: test repo at http://${ip}/Packages is not reachable from a container on the default bridge network." >&2
    exit 1
  fi
done
echo "Both repos reachable."

echo "== Rebuilding the trixie builder image =="
"${REPO_ROOT}/docker_deb_build.py" --rebuild --distro trixie --no-update-check

run_build() {
  local out_dir="$1"
  shift
  mkdir -p "$out_dir"
  "${REPO_ROOT}/docker_deb_build.py" \
    --source-dir "$PIN_TEST_PACKAGE" \
    --output-dir "$out_dir" \
    --distro trixie \
    --no-update-check \
    -e "$UPSTREAM_REPO_LINE" \
    -e "$QCOM_REPO_LINE" \
    "$@"
}

echo "== Run 1/2: without --extra-repo-priority (expected to FAIL - reproduces the bug) =="
if run_build "${WORK_DIR}/output-unpinned"; then
  echo "ERROR: build unexpectedly succeeded without pinning." >&2
  echo "This means the synthetic repos aren't actually conflicting the way this test expects." >&2
  exit 1
fi
echo "Confirmed: build failed as expected (upstream version won without pinning)."

echo "== Run 2/2: with --extra-repo-priority pinning the qcom repo (expected to SUCCEED - verifies the fix) =="
if ! run_build "${WORK_DIR}/output-pinned" --extra-repo-priority 500 --extra-repo-priority 1001; then
  echo "ERROR: build unexpectedly failed with pinning applied." >&2
  exit 1
fi
echo "Confirmed: build succeeded (qcom-patched version won with pinning)."

echo "== PASS: --extra-repo-priority regression test passed =="
