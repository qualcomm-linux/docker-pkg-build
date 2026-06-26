#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
#
# SPDX-License-Identifier: BSD-3-Clause-Clear
"""
notice_and_license.py

Helper module for create_data_tar.py:
  - Fetch NOTICE from the Qualcomm notice generation API
  - Download LICENSE.qcom-2 from meta-qcom GitHub repo
  - Strip usr/share/doc/<pkg>/ from extracted .deb trees
"""

import glob
import json
import os
import re
import shutil
import subprocess
import sys

import requests
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from color_logger import logger

NOTICE_API_URL = "https://notice-gen-api-ci.lvprd.oks.drekar.qualcomm.com/generate-notice"
LICENSE_QCOM_2_URL = "https://raw.githubusercontent.com/qualcomm-linux/meta-qcom/master/licenses/LICENSE.qcom-2"


def _find_source_dir(source_name: str) -> str:
    """Locate the repo root for source_name by finding its debian/control in CWD."""
    cwd = os.getcwd()
    try:
        result = subprocess.check_output(
            ["grep", "-Erl", f"^Source:[[:space:]]*{source_name}$", "--include=control", "."],
            text=True, cwd=cwd, stderr=subprocess.DEVNULL
        ).strip()
    except subprocess.CalledProcessError:
        return None

    for control_path in result.splitlines():
        repo_root = os.path.dirname(os.path.dirname(os.path.join(cwd, control_path)))
        if os.path.isdir(repo_root):
            return repo_root
    return None


def resolve_git_context(changes_path: str) -> tuple:
    """
    Derive (project_name, revision) from the source repo for the given .changes file.
    Returns (None, None) on any non-fatal condition, with a warning logged.
    """
    try:
        with open(changes_path, "r", encoding="utf-8", errors="ignore") as f:
            changes_text = f.read()
    except Exception as e:
        logger.warning(f"resolve_git_context: could not read .changes: {e}")
        return None, None

    source_match = re.search(r"^Source:\s*(\S+)", changes_text, re.MULTILINE)
    if not source_match:
        logger.warning("resolve_git_context: no Source: field in .changes")
        return None, None

    source_name = source_match.group(1)
    source_dir = _find_source_dir(source_name)
    if not source_dir:
        logger.warning(f"resolve_git_context: source dir for '{source_name}' not found")
        return None, None

    try:
        remote_output = subprocess.check_output(
            ["git", "-c", "safe.directory=*", "remote", "-v"],
            text=True, cwd=source_dir, stderr=subprocess.DEVNULL
        )
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True, cwd=source_dir, stderr=subprocess.DEVNULL
        ).strip()
    except subprocess.CalledProcessError:
        logger.warning("resolve_git_context: git command failed")
        return None, None

    match = re.search(r"29418/(.+?)(?:\s|\(|$)", remote_output)
    if not match:
        logger.warning("resolve_git_context: no Gerrit remote (port 29418) found")
        return None, None

    return match.group(1), revision


def fetch_notice(work_dir: str, project_name: str, revision: str) -> None:
    """
    Fetch NOTICE from the Qualcomm notice generation API and write to work_dir/NOTICE.
    Non-fatal: non-success API status, missing download_link.
    Fatal (raises): network/HTTP errors, JSON decode errors, file write errors.
    """
    if not project_name or not revision:
        logger.warning("fetch_notice: no git context available — skipping.")
        return

    payload = {"project_name": project_name, "revision": revision}
    logger.info(f"Fetching NOTICE for '{project_name}' at {revision[:12]}...")

    response = requests.post(
        NOTICE_API_URL,
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload),
        timeout=60
    )
    response.raise_for_status()
    result = response.json()

    if result.get("status") != "success":
        logger.warning(f"fetch_notice: API non-success — skipping. Response: {result}")
        return

    download_link = result.get("download_link")
    if not download_link:
        logger.warning("fetch_notice: no download_link in response — skipping.")
        return

    logger.info(f"Downloading NOTICE from: {download_link}")
    dl_response = requests.get(download_link, timeout=120)
    dl_response.raise_for_status()

    with open(os.path.join(work_dir, "NOTICE"), "wb") as nf:
        nf.write(dl_response.content)
    logger.info(f"NOTICE written to: {os.path.join(work_dir, 'NOTICE')}")


def fetch_license_qcom2(work_dir: str) -> None:
    """Download LICENSE.qcom-2 from meta-qcom and write to work_dir/LICENSE.qcom-2."""
    logger.info(f"Downloading LICENSE.qcom-2 from: {LICENSE_QCOM_2_URL}")
    response = requests.get(LICENSE_QCOM_2_URL, timeout=60)
    response.raise_for_status()

    with open(os.path.join(work_dir, "LICENSE.qcom-2"), "wb") as lf:
        lf.write(response.content)
    logger.info(f"LICENSE.qcom-2 written to: {os.path.join(work_dir, 'LICENSE.qcom-2')}")


def strip_doc_dirs(work_dir: str) -> None:
    """Remove usr/share/doc/<pkg>/ from each extracted package tree under work_dir/data/."""
    data_root = os.path.join(work_dir, "data")
    for pkg_dir in glob.glob(os.path.join(data_root, "*")):
        doc_dir = os.path.join(pkg_dir, "usr", "share", "doc")
        if os.path.isdir(doc_dir):
            shutil.rmtree(doc_dir)
            logger.debug(f"Removed doc dir: {doc_dir}")
