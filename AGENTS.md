# docker-pkg-build — Agent Guidelines

## Purpose

`docker-pkg-build` is a toolset that wraps `sbuild` and `gbp` inside Docker containers to build
Debian packages for ARM64 targets with a one-liner, without requiring the user to understand
chroots, schroot, or sbuild internals.

## Repository Layout

```
docker_deb_build.py        # Main entry-point script (the "one-liner" wrapper)
create_data_tar.py         # Helper used internally
color_logger.py            # Logging utilities
Dockerfiles/
  Dockerfile.<os>.<distro>         # One Dockerfile per target distro
  base-packages.txt                # Packages installed in the Docker image layer
  extra-packages.txt               # Packages installed inside the build chroot
  keyrings/
    qsc-deb-releases.asc           # Qualcomm APT repo PGP public key
    debusine.asc                   # Qualcomm Debusine qli repo PGP public key
  sources/
    <ubuntu-distro>/qsc-deb-releases.sources  # QArtifactory source entry
    <debian-distro>/qli.sources             # Debusine qli source entry
```

## Supported Distros

| Suite     | OS     | sbuild backend | Chroot format |
|-----------|--------|----------------|---------------|
| noble     | Ubuntu | unshare        | `/root/.cache/sbuild/noble-arm64.tar` (mmdebstrap) |
| resolute  | Ubuntu | unshare        | `/root/.cache/sbuild/resolute-arm64.tar` (mmdebstrap) |
| trixie    | Debian | unshare        | `/root/.cache/sbuild/trixie-arm64.tar` (mmdebstrap) |
| forky     | Debian | unshare        | `/root/.cache/sbuild/forky-arm64.tar` (mmdebstrap) |
| sid       | Debian | unshare        | `/root/.cache/sbuild/sid-arm64.tar` (mmdebstrap) |

## Key Design Decisions

- **Unshare backend**: All Debian/Ubuntu suites in this repo use sbuild's
  unshare backend with a tarball at
  `/root/.cache/sbuild/<distro>-<arch>.tar`.
  Dockerfiles use `mmdebstrap --format=tar` and disable sbuild's unshare
  auto-regeneration/max-age refresh so the customized tarball is not replaced
  with a plain one that lacks Qualcomm APT sources.
- **CA certificates in chroot**: The chroot tarball must include `ca-certificates` and `openssl`
  so that HTTPS APT repositories work inside the chroot at build time.
- **Qualcomm APT sources**:
  - Ubuntu chroots include `qsc-deb-releases.sources` from `qartifactory-edge.qualcomm.com`.
  - Debian `trixie` and `forky` chroots include `qli.sources` from `deb.debusine.qualcomm.com`.
  - `sid` includes no default Qualcomm source and relies on caller-provided `--extra-repo` when needed.

## Common Commands

```bash
# Build a package for a specific distro
docker_deb_build.py -s <source-dir> -o <output-dir> -d <distro>

# Rebuild the Docker images (after changing a Dockerfile)
docker_deb_build.py -d <distro> --rebuild

# Pass an additional APT repo at build time
docker_deb_build.py -s <source-dir> -o <output-dir> -d <distro> \
  -e "deb [arch=arm64 signed-by=/etc/apt/keyrings/qsc-deb-releases.asc] https://... <suite> main"
```

## When Editing Dockerfiles

- Changes to `base-packages.txt` or `extra-packages.txt` affect **all** distros.
- Changes to `keyrings/` or `sources/` affect **all** distros — check every `.sources` file if
  updating suite names.
- After any Dockerfile change, the corresponding Docker image must be rebuilt with `--rebuild`.
- **all Debian/Ubuntu suites**: any change to chroot content requires updating
  `mmdebstrap` `--customize-hook` or `--include` flags, not post-build copies.
