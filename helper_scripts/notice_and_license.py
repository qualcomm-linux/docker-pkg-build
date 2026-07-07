#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
#
# SPDX-License-Identifier: BSD-3-Clause-Clear
"""Helper functions for create_data_tar.py: collect NOTICE/LICENSE files, fetch LICENSE.qcom-2, strip doc dirs."""

import glob
import os
import re
import shutil
import subprocess
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from color_logger import logger

LICENSE_QCOM_2_URL = "https://raw.githubusercontent.com/qualcomm-linux/meta-qcom/master/licenses/LICENSE.qcom-2"


def _find_source_dir(source_name: str, search_root: str = None) -> str:
    """Locate the repo root for source_name by finding its debian/control under search_root."""
    root = os.path.abspath(search_root or os.getcwd())
    try:
        result = subprocess.check_output(
            ["grep", "-Erl", f"^Source:[[:space:]]*{source_name}$", "--include=control", "."],
            text=True, cwd=root, stderr=subprocess.DEVNULL
        ).strip()
    except subprocess.CalledProcessError:
        return None

    for control_path in result.splitlines():
        repo_root = os.path.dirname(os.path.dirname(os.path.join(root, control_path)))
        if os.path.isdir(repo_root):
            return repo_root
    return None


def _find_sources_dirs(start_dir: str) -> list:
    """Return every '<ancestor>/sources' directory found while walking upwards."""
    found = []
    current = os.path.abspath(start_dir)
    while True:
        candidate = os.path.join(current, "sources")
        if os.path.isdir(candidate):
            found.append(candidate)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return found


def _find_notice_and_license_files_in_repo(source_dir: str) -> tuple:
    """
    Return (repo_root, files), where files is a sorted list of absolute paths
    to every NOTICE and LICENSE file tracked by git at repo_root.
    Raises RuntimeError if git is unavailable or source_dir is not in a git repo.
    Returns an empty files list if none are found.
    """
    try:
        repo_root = subprocess.check_output(
            ["git", "-c", "safe.directory=*", "rev-parse", "--show-toplevel"],
            text=True, cwd=source_dir, stderr=subprocess.DEVNULL
        ).strip()
    except FileNotFoundError:
        raise RuntimeError("git is not installed or not on PATH")
    except subprocess.CalledProcessError:
        raise RuntimeError(
            f"git rev-parse failed in '{source_dir}' (not a git repo?)"
        )

    try:
        result = subprocess.run(
            ["git", "-c", "safe.directory=*", "ls-files", "--",
             "*/NOTICE", "NOTICE", "*/LICENSE", "LICENSE"],
            text=True, cwd=repo_root, capture_output=True, check=True
        )
        output = result.stdout.strip()
    except FileNotFoundError:
        raise RuntimeError("git is not installed or not on PATH")
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else ""
        raise RuntimeError(
            f"git ls-files failed in '{repo_root}': {stderr}"
        )

    files = sorted(
        os.path.join(repo_root, p)
        for p in output.splitlines()
        if p and os.path.isfile(os.path.join(repo_root, p))
    )
    return repo_root, files


def collect_notice(work_dir: str, changes_path: str) -> None:
    """
    Locate the source repo for the package described by changes_path, find every
    NOTICE and LICENSE file tracked in that repo, concatenate them (with a header
    per file when there are multiple), and write the result to work_dir/NOTICE.

    Non-fatal: if the source dir cannot be found or no files exist,
    a warning is logged and no file is written.
    Fatal (raises): git not available, source dir is not a git repo, file write errors.
    """
    try:
        with open(changes_path, "r", encoding="utf-8", errors="ignore") as f:
            changes_text = f.read()
    except Exception as exc:
        logger.warning(f"collect_notice: could not read .changes file: {exc}")
        return

    source_match = re.search(r"^Source:\s*(\S+)", changes_text, re.MULTILINE)
    if not source_match:
        logger.warning("collect_notice: no Source: field in .changes — skipping NOTICE collection")
        return

    source_name = source_match.group(1)
    search_roots = [
        os.path.abspath(work_dir),
        os.path.abspath(os.path.dirname(work_dir)),
        os.path.abspath(os.getcwd()),
    ]
    search_roots.extend(_find_sources_dirs(work_dir))
    search_roots.extend(_find_sources_dirs(os.getcwd()))
    search_roots = list(dict.fromkeys(search_roots))

    source_dir = None
    for root in search_roots:
        source_dir = _find_source_dir(source_name, search_root=root)
        if source_dir:
            break

    if not source_dir:
        logger.warning(f"collect_notice: source dir for '{source_name}' not found — skipping NOTICE collection")
        return

    repo_root, notice_files = _find_notice_and_license_files_in_repo(source_dir)
    if not notice_files:
        logger.warning(f"collect_notice: no NOTICE/LICENSE files found in '{repo_root}' — skipping")
        return

    logger.info(f"collect_notice: found {len(notice_files)} NOTICE/LICENSE file(s) in '{repo_root}'")

    sections = []
    for notice_path in notice_files:
        try:
            with open(notice_path, "r", encoding="utf-8", errors="replace") as nf:
                content = nf.read()
        except Exception as exc:
            logger.warning(f"collect_notice: could not read {notice_path}: {exc}")
            continue
        if len(notice_files) > 1:
            rel = os.path.relpath(notice_path, repo_root)
            content = f"{'=' * 72}\nFILE: {rel}\n{'=' * 72}\n\n{content}"
        sections.append(content)

    out_path = os.path.join(work_dir, "NOTICE")
    with open(out_path, "w", encoding="utf-8") as out_fh:
        out_fh.write("\n\n".join(sections))
    logger.info(f"NOTICE written to: {out_path}")


def fetch_license_qcom2(work_dir: str) -> None:
    """Download LICENSE.qcom-2 from meta-qcom and write to work_dir/LICENSE.qcom-2.

    Non-fatal: logs a warning and skips if the download fails (e.g. network
    unavailable in air-gapped environments).
    """
    logger.info(f"Downloading LICENSE.qcom-2 from: {LICENSE_QCOM_2_URL}")
    try:
        response = requests.get(LICENSE_QCOM_2_URL, timeout=60)
        response.raise_for_status()
    except Exception as exc:
        logger.warning(f"fetch_license_qcom2: could not download LICENSE.qcom-2: {exc} — skipping")
        return

    out_path = os.path.join(work_dir, "LICENSE.qcom-2")
    with open(out_path, "wb") as lf:
        lf.write(response.content)
    logger.info(f"LICENSE.qcom-2 written to: {out_path}")


def strip_doc_dirs(work_dir: str) -> None:
    """Remove usr/share/doc/<pkg>/ from each extracted package tree under work_dir/data/."""
    data_root = os.path.join(work_dir, "data")
    for pkg_dir in glob.glob(os.path.join(data_root, "*")):
        doc_dir = os.path.join(pkg_dir, "usr", "share", "doc")
        if os.path.isdir(doc_dir):
            shutil.rmtree(doc_dir)
