#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
#
# SPDX-License-Identifier: BSD-3-Clause-Clear
"""
create_data_tar.py

Standalone utility to:
- Locate a .changes file via --path-to-changes (file path or directory; if directory, the newest .changes is selected)
- Extract each referenced .deb into data/<pkg>/<arch>/ under the directory containing the .changes file
- Pack the data/ directory as <changes_basename>.tar.gz
- Place the tarball under <output-tar>/prebuilt_<distro>/ when --output-tar and --distro are provided; otherwise follow the fallback rules described in --output-tar help.

By default the script re-invokes itself inside a Docker container (as root) so
that it can always write to the output directory regardless of ownership (a
common situation after a Docker-based debian build where sbuild runs as root).
Pass --_in-docker internally (set automatically) to skip the re-invocation.
"""

import os
import sys
import argparse
import glob
import re
import tarfile
import shutil
import subprocess
import traceback
import json
import urllib.request
import urllib.parse
import urllib.error

from color_logger import logger
from helper_scripts.notice_and_license import collect_notice, fetch_license_qcom2, strip_doc_dirs

# Same image naming convention used by docker_deb_build.py
DOCKER_IMAGE_NAME_FMT = "ghcr.io/qualcomm-linux/pkg-builder:{suite_name}"
# Public Qualcomm Artifactory search endpoint used for duplicate-version guard.
ARTIFACTORY_SEARCH_API = "https://qartifactory-edge.qualcomm.com/artifactory/api/search/artifact"


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Generate data.tar.gz by extracting deb contents to data/<pkg>/<arch>/ from a .changes file."
    )
    parser.add_argument(
        "--path-to-changes",
        required=False,
        default=".",
        help="Path to the .changes file or a directory containing .changes files. If a directory is provided, the newest .changes will be used."
    )
    parser.add_argument(
        "--output-tar",
        required=False,
        default="",
        help="Base output directory where the tarball will be placed. When --distro is provided, the tarball will be written to <output-tar>/prebuilt_<distro>/"
    )
    parser.add_argument(
        "--arch",
        required=False,
        default="arm64",
        help="Architecture subfolder under each package directory (default: arm64)."
    )
    parser.add_argument(
        "--distro",
        required=False,
        default="",
        help="Target distro name (e.g., noble, questing). If provided, tar will be placed under <output-tar>/prebuilt_<distro>/"
    )
    parser.add_argument(
        "--docker-image",
        required=False,
        default="",
        help="Docker image to use when running inside a container. "
             "Defaults to ghcr.io/qualcomm-linux/pkg-builder:<distro>."
    )
    # Internal flag: set automatically when the script is already running inside
    # a container to prevent infinite re-invocation.
    parser.add_argument("--_in-docker", dest="in_docker", action="store_true",
                        default=False, help=argparse.SUPPRESS)
    return parser.parse_args()


def rerun_in_docker(args, changes_path: str) -> int:
    """
    Re-invoke this script inside a Docker container so it runs as root.
    This ensures write access to the output directory regardless of ownership.

    Mounts:
      <script_dir>      -> /scripts          (read-only: this script + color_logger.py)
      <work_dir>        -> <work_dir>        (read-write: .changes and .deb files)
      <base_output_dir> -> <base_output_dir> (read-write: tarball destination)
      <project_root>    -> <project_root>    (read-write: package source discovery for NOTICE)
      <invocation_cwd>  -> <invocation_cwd>  (read-write: all folders under $PWD, e.g. .repo/sources/out)

    work_dir and base_output_dir are mounted at their original absolute paths
    so all path arguments remain valid unchanged inside the container.
    If base_output_dir does not yet exist, Docker (running as root) creates it.

    Returns the container's exit code.
    """
    if args.docker_image:
        image_name = args.docker_image
    else:
        suite = args.distro if args.distro else "noble"
        image_name = DOCKER_IMAGE_NAME_FMT.format(suite_name=suite)

    script_dir      = os.path.dirname(os.path.abspath(__file__))
    project_root    = os.path.abspath(os.path.join(script_dir, os.pardir))
    work_dir        = os.path.dirname(changes_path)
    base_output_dir = os.path.abspath(args.output_tar) if args.output_tar else work_dir
    invocation_cwd  = os.path.abspath(os.getcwd())

    # Build a minimal set of data mounts (skip a path already covered by a
    # parent mount to avoid overlapping -v flags).
    candidates = {work_dir, base_output_dir, project_root, invocation_cwd}
    candidates = sorted(candidates)
    data_mounts = []
    for d in candidates:
        if not any(d == r or d.startswith(r + os.sep) for r in data_mounts):
            data_mounts.append(d)

    docker_cmd = ['docker', 'run', '--rm',
                  '-v', f'{script_dir}:/scripts:ro,Z']
    for d in data_mounts:
        docker_cmd += ['-v', f'{d}:{d}:Z']

    docker_cmd += [image_name, 'python3', '/scripts/create_data_tar.py',
                   '--path-to-changes', changes_path,
                   '--arch', args.arch]
    if args.output_tar:
        docker_cmd += ['--output-tar', base_output_dir]
    if args.distro:
        docker_cmd += ['--distro', args.distro]
    if args.docker_image:
        docker_cmd += ['--docker-image', args.docker_image]
    docker_cmd += ['--_in-docker']   # prevent recursive re-invocation

    logger.info(f"Running create_data_tar.py inside container '{image_name}' ...")
    res = subprocess.run(docker_cmd, check=False)
    return res.returncode


def find_changes_file(path_to_changes: str) -> str:
    """
    Return the path to the .changes file to use.
    If path_to_changes is a .changes file path, use it.
    If it is a directory, find the newest *.changes in that directory.
    """
    if not path_to_changes:
        path_to_changes = '.'

    path_to_changes = os.path.abspath(path_to_changes)

    if os.path.isfile(path_to_changes) and path_to_changes.endswith('.changes'):
        return path_to_changes

    if os.path.isdir(path_to_changes):
        candidates = glob.glob(os.path.join(path_to_changes, '*.changes'))
        if not candidates:
            raise FileNotFoundError(f"No .changes files found in directory: {path_to_changes}")
        newest = max(candidates, key=lambda p: os.path.getmtime(p))
        return os.path.abspath(newest)

    raise FileNotFoundError(f"Invalid --path-to-changes: {path_to_changes}. Provide a .changes file or a directory containing .changes files.")


def collect_debs_from_changes(changes_path: str):
    """
    Read the .changes file and collect referenced .deb filenames.
    Returns a list of basenames (or relative names) as they appear in the changes file.
    """
    try:
        with open(changes_path, 'r', encoding='utf-8', errors='ignore') as f:
            text = f.read()
    except Exception as e:
        raise RuntimeError(f"Failed to read .changes file {changes_path}: {e}")

    # Regex to capture *.deb tokens
    debs = [fn for _, fn in re.findall(r'(^|\s)(\S+\.deb)\b', text)]
    if not debs:
        # Fallback: simple tokenization
        for line in text.splitlines():
            if '.deb' in line:
                for tok in line.split():
                    if tok.endswith('.deb'):
                        debs.append(tok)

    # De-duplicate, keep order
    uniq = list(dict.fromkeys(debs))
    if not uniq:
        raise RuntimeError(f"No .deb files referenced in .changes file: {changes_path}")
    return uniq


def extract_debs_to_data(deb_names, work_dir, arch) -> bool:
    """
    For each deb in deb_names (relative to work_dir), extract with dpkg-deb -x
    into work_dir/data/<pkg>/<arch>/
    Returns True if at least one deb was extracted successfully.
    """
    data_root = os.path.join(work_dir, 'data')
    if os.path.isdir(data_root):
        shutil.rmtree(data_root)
    os.makedirs(data_root)

    extracted_any = False
    for deb_name in deb_names:
        deb_path = deb_name if os.path.isabs(deb_name) else os.path.join(work_dir, deb_name)
        if not os.path.exists(deb_path):
            logger.warning(f"Referenced .deb not found: {deb_path} (skipping)")
            continue

        base = os.path.basename(deb_path)
        # Expected: <pkg>_<version>_<arch>.deb, fall back to stem if no underscores
        pkg = base.split('_')[0] if '_' in base else os.path.splitext(base)[0]
        dest_dir = os.path.join(data_root, pkg, arch)
        os.makedirs(dest_dir, exist_ok=True)

        try:
            subprocess.run(['dpkg-deb', '-x', deb_path, dest_dir], check=True)
            extracted_any = True
        except FileNotFoundError:
            logger.error("dpkg-deb not found on host. Install dpkg tools to enable extraction.")
            return False
        except subprocess.CalledProcessError as e:
            logger.error(f"dpkg-deb failed extracting {deb_path}: {e}")

    if not extracted_any:
        logger.error("No .deb files were successfully extracted.")
        return False
    return True


def create_tar_of_data(work_dir: str, tar_path: str) -> str:
    """
    Create tarball at tar_path containing the data/ directory from work_dir.
    If present, also include NOTICE and LICENSE.qcom-2 at tar root.
    Returns the path to the tarball on success.
    """
    data_root = os.path.join(work_dir, 'data')
    if not os.path.isdir(data_root):
        raise RuntimeError(f"Missing data directory to archive: {data_root}")

    os.makedirs(os.path.dirname(tar_path) or '.', exist_ok=True)
    with tarfile.open(tar_path, 'w:gz') as tar:
        tar.add(data_root, arcname='data')
        for filename in ('NOTICE', 'LICENSE.qcom-2'):
            path = os.path.join(work_dir, filename)
            if os.path.isfile(path):
                tar.add(path, arcname=filename)
    return tar_path


def remove_stale_metadata_files(work_dir: str) -> None:
    """
    Remove stale metadata files from previous runs so we don't archive leftovers
    when the current package does not generate them.
    """
    for filename in ("NOTICE", "LICENSE.qcom-2"):
        path = os.path.join(work_dir, filename)
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError as e:
                raise RuntimeError(f"Failed to remove stale file '{path}': {e}")


def parse_tar_identity(tar_name: str):
    """
    Parse tar name as: <pkg>_<version>_<arch>.tar.gz
    Returns dict with pkg/version/arch on success, else None.
    """
    m = re.match(r'^(?P<pkg>[^_]+)_(?P<version>[^_]+)_(?P<arch>[^_]+)\.tar\.gz$', tar_name)
    if not m:
        return None
    return m.groupdict()


def fetch_artifactory_uris(query_name: str):
    """
    Query the public Artifactory search API and return list of result URIs.
    """
    query = urllib.parse.urlencode({"name": query_name})
    url = f"{ARTIFACTORY_SEARCH_API}?{query}"
    if not url.startswith("https://"):
        raise RuntimeError(f"Refusing to fetch non-HTTPS URL: {url}")
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Artifactory query failed ({e.code}) for {url}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Failed to reach Artifactory at {url}: {e}") from e

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Artifactory response was not valid JSON from {url}") from e

    results = data.get("results", [])
    if not isinstance(results, list):
        raise RuntimeError(f"Unexpected Artifactory response shape from {url}: missing list 'results'")
    uris = []
    for row in results:
        if isinstance(row, dict) and isinstance(row.get("uri"), str):
            uris.append(row["uri"])
    return uris


def fail_if_version_exists_in_artifactory(tar_name: str, args) -> None:
    """
    Query Artifactory and fail when the same package version is already present
    for the same architecture.
    """
    identity = parse_tar_identity(tar_name)
    if identity:
        query_name = identity["pkg"]
    else:
        # Fallback to exact filename search when tar name does not follow
        # <pkg>_<version>_<arch>.tar.gz.
        query_name = tar_name

    uris = fetch_artifactory_uris(query_name)

    # Scope duplicate checks to the same distro folder layout used for output:
    # /prebuilt_<distro>/...
    filter_re = re.compile(rf"/prebuilt_{re.escape(args.distro)}/") if args.distro else None
    duplicates = []
    for uri in uris:
        if filter_re and not filter_re.search(uri):
            continue
        base = os.path.basename(uri)
        if not base.endswith(".tar.gz"):
            continue
        if identity:
            other = parse_tar_identity(base)
            if not other:
                continue
            if (other["pkg"] == identity["pkg"]
                    and other["version"] == identity["version"]
                    and other["arch"] == identity["arch"]):
                duplicates.append(uri)
        else:
            if base == tar_name:
                duplicates.append(uri)

    if duplicates:
        examples = "\n".join(f"  - {u}" for u in duplicates[:10])
        extra = "" if len(duplicates) <= 10 else f"\n  ... and {len(duplicates) - 10} more"
        raise RuntimeError(
            "Version already exists in Artifactory; refusing to create tarball.\n"
            f"  tar_name: {tar_name}\n"
            f"  matches: {len(duplicates)}\n"
            f"{examples}{extra}"
        )


def main():
    args = parse_arguments()

    # Determine the .changes file
    try:
        changes_path = find_changes_file(args.path_to_changes)
    except Exception as e:
        logger.critical(str(e))
        sys.exit(1)

    # Always run inside Docker (as root) to ensure write access to the output
    # directory, which is typically owned by root after a Docker-based build.
    if not args.in_docker:
        rc = rerun_in_docker(args, changes_path)
        sys.exit(rc)

    # The working directory is where the .changes was generated (and where the debs are expected)
    work_dir = os.path.dirname(changes_path)
    base = os.path.basename(changes_path)
    tar_name = re.sub(r'\.changes$', '.tar.gz', base)
    if tar_name == base:
        tar_name = base + '.tar.gz'

    # Remote duplicate guard: fail early if this version already exists.
    try:
        fail_if_version_exists_in_artifactory(tar_name, args)
    except Exception as e:
        logger.critical(str(e))
        sys.exit(1)

    # Remove stale metadata files from previous runs.
    try:
        remove_stale_metadata_files(work_dir)
    except Exception as e:
        logger.critical(str(e))
        sys.exit(1)

    # Collect debs from the changes file
    try:
        deb_names = collect_debs_from_changes(changes_path)
    except Exception as e:
        logger.critical(str(e))
        sys.exit(1)

    # Extract each deb into data/<pkg>/<arch>/
    ok = extract_debs_to_data(deb_names, work_dir, args.arch)
    if not ok:
        sys.exit(1)

    # Collect NOTICE/LICENSE files, fetch LICENSE.qcom-2, strip doc dirs.
    collect_notice(work_dir, changes_path)
    fetch_license_qcom2(work_dir)
    strip_doc_dirs(work_dir)

    # Create tarball named after the .changes file (e.g., pkg_1.0_arm64.tar.gz)
    try:
        # Determine destination tar path based on --output-tar and --distro
        if args.output_tar:
            base_output_dir = os.path.abspath(args.output_tar)
            dest_dir = os.path.join(base_output_dir, f'prebuilt_{args.distro}') if args.distro else base_output_dir
            tar_path = os.path.join(dest_dir, tar_name)
        else:
            # Fallback to work_dir if no explicit output tar path is provided
            dest_dir = os.path.join(work_dir, f'prebuilt_{args.distro}') if args.distro else work_dir
            tar_path = os.path.join(dest_dir, tar_name)
        tar_path = create_tar_of_data(work_dir, tar_path)
        logger.info(f"Created tarball: {tar_path}")
    except Exception as e:
        logger.critical(f"Failed to create tarball: {e}")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.critical(f"Uncaught exception: {e}")
        traceback.print_exc()
        sys.exit(1)
