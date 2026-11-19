# DREAMM v1.1 standalone Linux installation

## Before installing

Use a normal non-root account on x86-64 Linux. The guided basic and full profiles were qualified on Ubuntu 22.04 under WSL with glibc 2.35. The pinned native runtime requires glibc 2.35 or later. Rocky Linux 9 ships a different glibc baseline and needs separate qualification. The CLI uses no webserver, systemd service, Conda or dedicated cgroup delegation.

Install Python 3.12 or later for the setup script, GCC/G++, make, git and bubblewrap with working unprivileged user, PID, network and mount namespaces. For full mode also provide the distribution fonts and Fontconfig. The setup script operates in user-owned directories.

Allow at least 14 GiB available RAM at admission. One scientific worker runs at a time. The worker aborts on sampled aggregate RSS above 12 GiB or available host memory below 2 GiB. Allow at least 6 GiB on the runtime volume plus job storage. The default 2 GiB scratch budget needs 4 GiB free where the worker stages jobs and 2 GiB on the output volume. These are trusted-local research controls. The public worker has a separate admission and isolation contract.

UniClust30 2018_08 needs 86.4 GiB unpacked. Allow about 140 GiB free for initial download, validation, extraction and reserve. Prefer an SSD on a local Linux filesystem. WSL-mounted NTFS can introduce substantial database mmap I/O overhead.

## Install

Before a fresh installation, obtain these external prerequisites under their applicable terms.

- A lawfully held original DREAMM scientific asset checkout containing the pinned source revision `481055593c5676fb8f2a29c13e776782d772e527`. The installer uses its original model, reference, configuration, ProtDCal 4.5 and companion assets. See the [publication and dependency rights gate](THIRD_PARTY_NOTICES.md#release-gate).
- The official [MSMS 2.6.1 release](https://github.com/sannerlab/downloads/releases/tag/msms-2.6.1) archive `msms_i86_64Linux2_2.6.1.tar.gz`.
- For the full profile, the official [UCSF Chimera Classic 1.19 Linux headless installer](https://www.rbvi.ucsf.edu/chimera/download.html) `chimera-1.19-linux_x86_64_osmesa.bin`. Review and accept vendor terms yourself.
- For all profiles, check that you are eligible under the academic terms in the pinned MoleculeKit wheel. See [third-party prerequisites](THIRD_PARTY_NOTICES.md).

Run one setup command from the source tree. Use `--database-volume /path/to/large-local-volume` in place of `--database` to acquire the exact database on a suitably sized disk.

```sh
python3 -I -S -B tools/install.py --profile basic \
  --database /data/uniclust30_2018_08 \
  --msms-archive ~/Downloads/msms_i86_64Linux2_2.6.1.tar.gz \
  --asset-repo ~/original-DREAMM-assets
```

For M1–M5 change to `--profile full` and add `--chimera-installer ~/Downloads/chimera-1.19-linux_x86_64_osmesa.bin`. Installation downloads permitted pinned Python, Java and native science assets, builds ProDy and FreeSASA, builds the original ProtDCal finish helper, checks the complete installed closure and verifies the exact local database. Full mode also stages P2Rank, XLSX support, Julia, ProteinEnsembles and the verified plotting overlay. The setup script passes SHA-256 values to the existing installers internally. It reuses completed steps only after their integrity checks. Failure leaves diagnostic files and does not publish a successful configuration.

The default installation prefix is `~/.local/share/dreamm`. The setup script places the launcher under its `bin/` directory and the private configuration under `$XDG_CONFIG_HOME/dreamm/config.json` (usually `~/.config/dreamm/config.json`). Original verified downloads are retained under `cache/`. Advanced users can choose another `--prefix` and `--config`.

```sh
export PATH="$HOME/.local/share/dreamm/bin:$PATH"
dreamm --version
```

## Run

| Mode | Example | Input and behaviour |
| --- | --- | --- |
| M1 | `dreamm run -i 1RLW -c A` | Official PDB entry or local PDB/mmCIF. Compute reference membrane residues using the first model. |
| M2 | `dreamm run --mode M2 -i protein.pdb` | Local PDB/mmCIF. Classify the first model. |
| M3 | `dreamm run --mode M3 -i 2KS4` | Official PDB entry or local PDB/mmCIF. Analyse the deposited models in order. |
| M4 | `dreamm run --mode M4 -i ensemble.pdb` | Local PDB/mmCIF. One or multiple source models. |
| M5 | `dreamm run --mode M5 -i 1RLW --additional 2` | Official PDB entry or local PDB/mmCIF. Generate 1–50 additional ExProSE conformations. The reference is included as frame zero. |

Use `-c A B` (or `-c A,B`) to select author chains. With no selector, DREAMM works on all admitted protein chains together. Modifications use the original DREAMM conversion table. Unknown chemistry, ambiguous identity, precision loss or an unrepresentable tool input fail with a specific stage error. The tool-specific projections currently require representable PDB chain/atom/residue fields. Requesting a PDB ID fetches the deposited coordinate entry over HTTPS and retains its identity and hash. Use a saved local structure to reproduce an earlier deposition exactly.

Use `--output DIRECTORY` to choose a new result directory. The default is a new named directory in the current working directory. Progress without a completion receipt is an incomplete attempt. Results preserve native artifacts, input provenance, membrane predictions, final flags and the original feature evidence. Single-structure exports include CSV, prepared PDB, PyMOL PML and ZIP. Ensemble ZIPs include source, prepared and aligned frames, per-frame pocket predictions, interface associations, ESSA and Conservation, ranked sites, CSV, XLSX and PML. M5 also retains generation coordinates and scientific analysis output. A calculated empty result remains explicitly empty.

## Expert options and recovery

Use `dreamm run --help` for the existing explicit source/runtime/checkpoint options. Keep `--full-database-verify` for a forced full scan. An unchanged full verification receipt reduces startup I/O. A metadata change forces full verification before reuse. The saved database is mounted read-only in the scientific worker.

The default local budgets are 64 MiB source, 128 MiB per evidence file and 2 GiB scratch. Override them with `--source-mib`, `--artifact-mib` and `--scratch-mib` within the documented CLI bounds and available disk. The practical scientific limit depends on tool representation and measured resources. `--timeout` sets the job deadline. Use `--stage-timeout STAGE=SECONDS` for a reference stage or the ensemble `generation` or `essa` stage. `-p` caps local CPU affinity. It leaves the scientific tool thread settings, including 15 HHblits threads, intact.

A fresh ensemble workflow computes its reference once. Advanced `export-reference` and `export-generation` freeze completed stages for reuse through the existing checkpoint switches. Supply the original source and the same verified settings when resuming. Match the reference mode, selected chains, source hash and generation parameters. Keep `--execution bounded-worker` and all of its original admission arguments for that separate mode. The configured CLI is for trusted-local research only.

## Validation scope

The v1.1 guided installer built the basic runtime in a fresh prefix, hashed the existing UniClust30 database, then added the full profile to that newly installed basic runtime. Original prerequisite downloads were reused by hash. Both profiles produced the same reviewed v1.1 launcher.

The accepted reference implementation completed clean Linux installation and an actual new-input 1PGA M2 run. Retained controls include 2,168 matching raw labels and 88 matching final calls across four M1 fixtures, native 1RLW M1/M2, 20-frame 2KS4 M3/M4, an M5 native empty result and positive downstream replay, and singleton M5 generation. Fresh HHblits searches can produce Conservation differences. Historical scaled feature matrices or raw labels are unavailable for some comparisons.

The v1.1 launcher also completed a fresh native M2 run of a synthetic two-chain fixture derived from public 1PGA coordinates. It preserved all 112 residue identities, including numbering shared by both chains and the inserted residue B:11A. The original classifier evaluated 96 eligible residues and returned nine membrane calls, including B:11A. The 499 descriptor columns, 727 ordered model features, chain-local flags and 92-member result ZIP were verified. This test qualifies the exercised M2 input path. Modified residues and the other modes retain their separate validation scope.

## Licences and redistribution

DREAMM is [GPL-3.0](LICENSE). Review the [third-party notices](THIRD_PARTY_NOTICES.md), including the separate acquisition requirements and unresolved ProtDCal publication scope. Distribute the matching source with the CLI when publication is authorised.
