# Install DREAMM v1.1 on Linux

Choose **basic** for membrane-residue prediction (M1/M2). Choose **full** if you also need interface pockets from supplied conformations (M3/M4) or want ExProSE to generate conformations (M5). Full installs all three ensemble modes, including Julia/ExProSE and spreadsheet exports. The reference prediction is identical in both bundles.

## Before you start

1. **Use a regular account on x86-64 Linux with glibc.** You need a writable private installation directory, `python3` (3.9 or later), Git, GCC, G++, make, tar, and working HTTPS certificate verification for downloads. If your system already provides these, go straight to step 2. Host Python starts the installer. DREAMM supplies scientific Python 3.14.10 and Java 21, builds ProDy and FreeSASA, and compiles its Java helper. No separate Java, Julia or Conda setup is needed.
2. **Download MSMS 2.6.1 for Linux x86-64.** Review the [vendor terms](THIRD_PARTY_NOTICES.md) and download `msms_i86_64Linux2_2.6.1.tar.gz` from the [official release](https://github.com/sannerlab/downloads/releases/tag/msms-2.6.1). Save the unopened archive in `~/Downloads/`, or enter its path at the installer prompt. Both profiles accept the current 597,469-byte archive and a lawfully retained 593,147-byte original. The installer verifies the archive automatically.
3. **Choose a location for UniClust30 2018_08 (HH-suite format).** If you have the exact unpacked database, use it in place. Otherwise choose a writable directory on a filesystem that supports symbolic links. The installer downloads and verifies the archive, then verifies the expanded files. Fresh acquisition needs approximately **123.24 GiB free** at the currently published download size. See [disk space](#disk-space) for reuse, interrupted downloads and runtime storage.
4. **Allow network access for first setup.** The installer fetches version-pinned dependencies over HTTPS. Full setup also uses Julia’s package server and package/artifact sources to populate a new private depot, even if you supply all of DREAMM’s cached download archives.

If system tools are missing, use your distribution’s packages or ask your system administrator to provide them. For example, on a Debian/Ubuntu system with administrative access and a suitable Python version:

```sh
sudo apt update
sudo apt install python3 git build-essential tar ca-certificates
```

Run DREAMM’s installer as your own user.

## Install and run your first prediction

Use a full Git clone of the `dev` branch for v1.1. The default branch contains the historical version. When testing a review branch, substitute its name for `dev` in the clone command.

```sh
git clone --branch dev https://github.com/AChatzigoulas/DREAMM.git
cd DREAMM
./install.sh
```

On a fresh interactive install, press Enter for **basic**, or enter **full**. If the installer cannot find MSMS, it asks for the archive path. At the database prompt, enter the directory containing the unpacked database files, or press Enter and give a directory for the download. An existing installation reuses its configured database unless you specify another. Without an interactive terminal, the default for a new installation is basic and missing external paths must be supplied explicitly.

```sh
export PATH="$HOME/.local/bin:$PATH"
dreamm --version
dreamm run --mode M2 -i examples/1rlw/source.pdb
```

The example uses the structure included in the checkout. Open `exports/membrane.csv` under the results directory printed by the command. For accession input, use `dreamm run -i 1RLW -c A`. See the [five mode examples](README.md#choose-a-workflow) and the [guide to inputs and results](USAGE.md).

The default installation lives in `~/.local/share/dreamm` with its launcher in `~/.local/bin`. Add the PATH line to your shell startup file if needed. To upgrade a basic installation, return to the same checkout and run `./install.sh --profile full` with the same prefix and configuration. Rerunning the installer on a full installation retains full support.

## Disk space

| Where | What to allow for |
| --- | --- |
| Existing database | The eleven regular database files total **86.38 GiB**, plus two symbolic links. DREAMM reads and verifies the directory in place. No second copy or download reserve is needed. |
| New database download | The current archive is **24.86 GiB**. Allow for the outstanding archive bytes **plus 86.38 GiB** for extraction and a **12 GiB free-space reserve** on the same volume. The installer retains the compressed archive. A complete cached archive needs only the extraction space and reserve (**98.38 GiB** additional free). |
| Installation directory | Installing missing components requires **6 GiB free** at preflight. A fresh Julia extraction also checks for 3 GiB free at that stage. For scale, one tested full installation occupied **about 4 GiB**, including the 1.7 GiB core runtime, local builds and download cache, with the database on another volume. Rechecking a complete installation skips the 6 GiB reservation. |
| Scientific runs | Allow separate space for temporary files, per-frame coordinates, intermediate features and the result ZIP. Requirements vary with protein size and frame count. If the database, prefix and runs share one filesystem, budget for all three. |

If allocating disk in whole GiB, allow at least **124 GiB free** for a fresh database download, or **99 GiB free** to extract an existing verified archive. These figures include the 12 GiB reserve.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| Missing MSMS or archive mismatch | Download the Linux x86-64 archive from the official release linked above and keep its filename intact. The installer accepts the two revisions in the [native lock](runtime/trusted-native.json). |
| Missing compiler, failed native build or shared library error | Check the named tool or library on the host. Use a compatible Linux build environment. The tested platform is listed below. |
| Database verification failed | Select the directory that directly contains the exact 2018_08 files and their symbolic links. Keep download archives and unrelated files outside that directory. |
| Julia package acquisition failed during full setup | Check HTTPS connectivity to Julia’s package server and artifact sources. The installation directory’s `julia-install/stderr` records the underlying error. An archive cache alone cannot supply a fresh Julia depot. |
| Missing or unreadable text in M5 plots | ExProSE uses Julia’s bundled Fontconfig library. It still needs a discoverable font with the label glyphs. An offline test rendered the labels with the Ubuntu font family alone. If text is missing, make a suitable font available to the renderer. DejaVu is one option. |
| Missing historical model or ProtDCal assets | Use the full Git checkout above. A ZIP or shallow clone can lack the required historical Git objects. See [other installation options](#other-installation-options). |
| `dreamm` command missing | Use the PATH line printed by the installer. A custom prefix puts the launcher under its own `bin/`. |
| Run stopped or produced no sites | Check for the top-level `complete.json` and read the stage error, if any. A completed zero-site result has a summary and empty site tables. See [results](USAGE.md#find-the-outputs). |

## Other installation options

For unattended installation, supply the external paths. Use `--database-volume DIR` in place of `--database DIR` to acquire the database. To install only the core tools, select `--profile basic`.

```sh
./install.sh --profile full --database /data/uniclust30_2018_08 \
  --msms-archive ~/Downloads/msms_i86_64Linux2_2.6.1.tar.gz
```

`--reuse-downloads DIR` rechecks and copies matching archives into the installation cache and downloads missing ones. A matching prior database verification can be reused while the installation and file metadata remain unchanged. To force a full content check on a run, add `--full-database-verify`. For other options, run `./install.sh --help`.

A full Git checkout includes the historical commit for the original model, training reference and ProtDCal. For the curated source ZIP, use `--asset-repo DIR` with a lawfully held checkout containing that commit. The CLI zipapp alone requires an installed runtime and the database. See the [distribution inventory](THIRD_PARTY_NOTICES.md).

### Performance and longer runs

An SSD can improve HHblits database access. Under WSL, a native Linux filesystem is preferable to a Windows-mounted drive for search performance. Neither is a substitute for the required file and link checks. Allow enough RAM for your structure and other work on the host. The local `--processes N` option limits CPU affinity. The scientific HHblits search still requests 15 threads.

The default overall deadline is five hours. For longer runs, use `--timeout SECONDS` and, where needed, `--stage-timeout STAGE=SECONDS` (for example, `hhblits`, `generation`, `p2rank` or `essa`). See `dreamm run --help` for local resource options. For reuse of a completed reference or generated ensemble, see `dreamm export-reference --help` and `dreamm export-generation --help`.

## Tested platform

The installation evidence covers **Ubuntu 22.04 on WSL2 (x86-64, glibc 2.35)**, including a fresh non-root basic installation followed by a full upgrade. Rocky Linux 9 and other distributions remain unqualified. See the [scientific validation scope](DEVELOPERS.md#standalone-validation) for what was exercised.
