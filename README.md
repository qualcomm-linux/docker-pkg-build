# docker_deb_build

A Python-based tool for building Debian packages inside Docker containers, designed for Qualcomm Linux projects. It supports multiple architectures (amd64, arm64) and Ubuntu versions (noble, questing) to ensure consistent and reproducible package builds.

## Branches

**main**: Primary development branch. Contributors should develop submissions based on this branch and submit pull requests to this branch.

## Requirements

- Python 3.6+
- Docker
- Git (for cloning repositories)

## Installation Instructions

1. Clone the repository:
   ```bash
   git clone https://github.com/qualcomm-linux/docker_deb_build.git
   cd docker_deb_build
   ```

2. Ensure Docker is running and you have permissions to build containers.

## Usage

Run the `docker_deb_build.py` script to build Debian packages:

```bash
python3 docker_deb_build.py --help
```

### Key Features

- **Docker-based Builds**: Packages are built inside isolated Docker containers to ensure reproducibility.
- **Multi-Architecture Support**: Includes Dockerfiles for amd64 and arm64 architectures.
- **Ubuntu Versions**: Supports noble and questing Ubuntu variants.
- **Automated Workflows**: Integrates with GitHub Actions via the `container-build-and-upload` workflow for CI/CD.

### Docker Images

The `docker/` folder contains pre-configured Dockerfiles:
- `Dockerfile.amd64.noble`: For AMD64 builds on Ubuntu Noble.
- `Dockerfile.amd64.questing`: For AMD64 builds on Ubuntu Questing.
- `Dockerfile.arm64.noble`: For ARM64 builds on Ubuntu Noble.
- `Dockerfile.arm64.questing`: For ARM64 builds on Ubuntu Questing.

### GitHub Workflow

The repository includes a `container-build-and-upload` workflow (located in `.github/workflows/`) that automates building and uploading Docker containers for package builds.

## Development

To contribute:
1. Fork the repository and create a feature branch from `main`.
2. Make your changes, ensuring tests pass.
3. Submit a pull request with a clear description of the changes.

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

## Getting in Contact

- [Report an Issue on GitHub](../../issues)
- [Open a Discussion on GitHub](../../discussions)
- [E-mail us](mailto:REPLACE-ME@qti.qualcomm.com) for general questions

## License

docker_deb_build is licensed under the [BSD-3-clause License](https://spdx.org/licenses/BSD-3-Clause.html). See [LICENSE.txt](LICENSE.txt) for the full license text.
