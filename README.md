# docker_deb_build

A Python-based tool for building Debian packages inside Docker containers, designed for Qualcomm Linux projects.
It supports multiple architectures (amd64, arm64) and Ubuntu versions (noble, questing) to ensure consistent and reproducible package builds.

This repo encompases two use cases: 
- Builder-agnostic local builds for a users wanting to build debian packages on their local machines in a repeatable way.
- Creating a docker image with required tooling and chroots for the different Qualcomm repo workflows.

See the **Github Workflow** section below.

## Branches

**main**: Primary development branch. Contributors should develop submissions based on this branch and submit pull requests to this branch.

## Requirements

- Python 3.6+
- Docker
- Git (for cloning repositories)

## Installation Instructions

1. Clone the repository \[in some tooling folder\]:
   ```bash
   git clone https://github.com/qualcomm-linux/docker_deb_build.git
   ```
   
2. Ensure Docker is running and you have permissions to build containers. The build scripts does multiple pre-flight checks.
   Should any test fail, there will be instructions on what to do to fix the issue.

3. Pro tip: Say you cloned this to your home folder, create a quick alias in your .bashrc file (debb == **deb**ian **b**uild:
   ```
   alias debb="~/docker_deb_build/docker_deb_build.py"
   ```

## First time using

Whenever you use the docker_deb_build script for the first time, it will need to build the containers.
You can either defer this step to when you build your first package, or you can manually trigger the build
of the containers as a confirmation step : 

```
./docker_deb_build.py --rebuild
```

The build should take around ~15 min.
After that, you can inspect the built images : 

```
docker image ls
```

You should then see the following (if you built on an X64_64 machine):

```
$ docker image ls
REPOSITORY                           TAG              IMAGE ID       CREATED          SIZE
ghcr.io/qualcomm-linux/pkg-builder   amd64-noble      efc7d13f70f2   42 seconds ago   2.41GB
ghcr.io/qualcomm-linux/pkg-builder   amd64-questing   3c037366744a   13 days ago      2.33GB
```

You can see that in our case, two containers have been built: one for noble and one for questing.
As time goes by, we will add more suites (questing, resolute, etc)
You can see in the TAG that the containers's tag is prepended with amd64 to show that those containers
(vs the amd64 ones) are for amd64 host to cross-compile for arm64. 

In the amd64 containers, they already contain everything to cross compile for arm64.

You can always build/rebuild the images with the command above.

## Keeping builds fast

Internally, the docker_deb_build tool uses sbuild to build your package and needs a chroot to do that.
Each of the containers contain one chroot. Every time sbuild is invoked, one of the first thing it does
is issuing a apt-get update + upgrade inside the chroot to make sure you have the latest version of everything.

As time goes by and as new packages version come up compared to when you built the container, the apt update/upgrade
step will take incrementally more time and basically repeat installing new versions eeeeevery time.

Therefore, in order to keep your build times minimal, you will want to rebuild your container periodically.

Every week or two is a good idea.

## Building an hello-world example 

You can test building the hello-world style pkg-example to prove everything works.
Head over to the [pkg-example](www.github.com/qualcomm-linux/pkg-example) page and have a look at the readme.

Then, clone and build :

```
alias debb=<docker_deb_build location>/docker_deb_build.py

git clone git@github.com:qualcomm-linux/pkg-example.git

mkdir build

debb --source-dir pkg-example --output-dir build --distro questing
```

## Usage

Run the `docker_deb_build.py` script to build Debian packages:

```bash
docker_deb_build.py --help
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

To add a new suite (like Trixie, Resolute, etc), copy the two Dockerfile (amd64 and arm64) for a given suite (say Questing) and tweak them to reflect the new version.
Then, in the **docker_deb_build** script, add a new line for that suite in :
```
    if args.rebuild:
        rebuild_docker_image(image_base, build_arch, 'noble')
        rebuild_docker_image(image_base, build_arch, 'questing')
        <HERE>
        sys.exit(0)
```
This will ensure that the new suite is built when running **$docker_deb_build.py --rebuild**
The last step is to ensure the containers (amd64 and arm64) for that suite are also pushed to GHCR as part of the **container-build-and-upload** workflow
by adding a new line in the _.github/actions/build_container/action.yml_ in the _Push to GHCR_ step : 
```
   echo ${{inputs.token}} | docker login ghcr.io -u ${{inputs.username}} --password-stdin
   docker push ghcr.io/${{env.QCOM_ORG_NAME}}/${{env.IMAGE_NAME}}:${{inputs.arch}}-noble
   docker push ghcr.io/${{env.QCOM_ORG_NAME}}/${{env.IMAGE_NAME}}:${{inputs.arch}}-questing
   <copy-paste one of the above docker line and modify the suite name at the end>
```

### GitHub Workflow

The repository includes a `container-build-and-upload` workflow (located in `.github/workflows/`) that automates building and uploading Docker containers for package builds.
This workflow is automatically executed every week so that the GHCR registry where the images are stored contains a one-week-or-less old image. This keeps build time as small as possible for workflows relying on those images. This is because when building using sbuild, the first step is doing an apt update; the older the image, the longer it takes doing this apt upgrade.

This also applies for non-github-workflow local builds; doing a **docker_deb_build.py --rebuild** periodically ensures a recent image and reduces the apt upgrade time at the start of every build.

## Adding tooling

If additional tooling is required, the user shall add it in every Dockerfile found in Docker, open and merge the PR which will automatically trigger a post-merge build and upload to GHCR. Then, next time a github workflow build happens, the new tool will be present in the image hosted in GHCR.

## How to enter the container

For whatever reason, you may have to enter the container in interactive mode. It could be testing installing extra tooling to make a build pass.
Note: think about adapting the tag for your scenario (arm vs amd, and suite)

```
docker run --rm -it --name pkg-builder-questing ghcr.io/qualcomm-linux/pkg-builder:amd64-questing bash
```

## Development

To contribute:
1. Fork the repository and create a feature branch from `main`.
2. Make your changes, ensuring tests pass.
3. Submit a pull request with a clear description of the changes.

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

## Getting in Contact

- [Report an Issue on GitHub](../../issues)
- [Open a Discussion on GitHub](../../discussions)
- [E-mail me](mailto:sbeaudoi@qti.qualcomm.com) for general questions

## License

docker_deb_build is licensed under the [BSD-3-clause License](https://spdx.org/licenses/BSD-3-Clause.html). See [LICENSE.txt](LICENSE.txt) for the full license text.
