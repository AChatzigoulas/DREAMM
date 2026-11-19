# DREAMM v1.1 standalone Linux installation

## Before installing

Install as a regular user on x86-64 Linux. The platform exercised here is Ubuntu 22.04 under WSL. The installer checks its build prerequisites and loads the pinned executables on the host. FreeSASA and ProDy are built locally. The pinned binary packages still have platform ABI requirements, and Rocky Linux 9 has not been qualified.

Install your distribution's Python 3.9 or later, Git, GCC/G++, make, tar and system CA certificates. For full mode, also install Fontconfig and a standard TrueType font. On Ubuntu 22.04, the usual packages are `python3 git build-essential tar ca-certificates fontconfig fonts-dejavu-core`. Installation needs approximately 6 GiB of free space for the runtime, plus disk space for scientific runs.

UniClust30 2018_08 needs 86.4 GiB unpacked. Reserve about 140 GiB for initial download and extraction. Use a local SSD if possible. Database I/O from a Windows-mounted drive under WSL can be slower than native Linux storage.

## Install

Obtain the original [vendor MSMS 2.6.1 archive](https://github.com/sannerlab/downloads/releases/tag/msms-2.6.1), named `msms_i86_64Linux2_2.6.1.tar.gz`, under the applicable terms. Keep that filename. Review the [MoleculeKit and other dependency terms](THIRD_PARTY_NOTICES.md). Use a Git checkout containing the original DREAMM scientific assets. A checkout of this branch normally already has them.

```sh
./install.sh
```

Choose basic for M1/M2 or full for M1–M5. The installer finds the MSMS archive in Downloads when present and prompts for its path otherwise. It asks for the existing database directory or a local download destination. All hashes are checked internally. An equivalent noninteractive command is

```sh
./install.sh --profile full --database /data/uniclust30_2018_08 \
  --msms-archive ~/Downloads/msms_i86_64Linux2_2.6.1.tar.gz
```

Use `--database-volume /path/to/large-local-volume` to download instead. Use `--reuse-downloads DIR` if original locked archives are already available. The installer verifies each reused archive. For a source-only archive or shallow clone missing the original assets, provide a lawfully held checkout using `--asset-repo DIR`.

The default prefix is `~/.local/share/dreamm`. The launcher goes to `~/.local/bin/dreamm` and reads its installed configuration automatically. A custom `--prefix` uses its own `bin/` directory. The installer supplies the PATH command if needed. Full mode installs P2Rank, XLSX support, Julia, and the pinned ExProSE package and PCA patch.

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

Use `dreamm run --help` for checkpoint and runtime options. A completed database verification receipt is reused while the underlying metadata and installation are unchanged. `--full-database-verify` forces another full scan.

The local CLI runs tools as the invoking user with ordinary process-group cleanup and a five-hour default job deadline. Set `--timeout` and `--stage-timeout STAGE=SECONDS` as needed. `--processes N` limits CPU affinity while leaving the scientific thread settings intact, including the 15 HHblits threads. `--memory-mib`, `--source-mib`, `--artifact-mib`, and `--scratch-mib` are optional local limits. Memory and scratch use are sampled. Size your runs for the actual available RAM and disk. The local path is intended for trusted research input. Public deployments use the separate bounded-worker and coordinator controls.

M5 uses explicit CCD and terminal bond templates to remove carbon-bound hydrogens before DSSP. Ambiguous topology fails with the affected residue and atom. If needed, obtain Chimera 1.19 under its own licence, pass `--chimera-installer` when setting up the full runtime, and select the fallback with `dreamm run --mode M5 … --chimera`. ExProSE’s original reader drops insertion codes. These inputs remain supported for the reference classifier, but native M5 correspondence cannot be guaranteed for them.

An ensemble run computes its reference once. Expert commands `export-reference` and `export-generation` freeze completed stages for reuse. Checkpoints retain exact source, chain, runtime and generation identities. Keep the same generation runtime for saved ExProSE checkpoints. The result directory keeps partial files and process logs if a run fails.

## Validation scope

The frozen `dev` baseline qualified a clean basic/full installation and native M1–M5 cases, including a 112-residue, two-chain M2 fixture with insertion B:11A. Historical comparisons with missing raw labels or scaled features remain unavailable. Independent fresh HHblits searches can vary numerically.

On Ubuntu 22.04 under WSL, both installation profiles completed. A direct one-frame M4 run and a direct two-frame M5 run produced verified ZIPs. M5 used the template-based preparation and the frozen 1RLW reference. The three retained polar structures matched their Chimera references at the atom and DSSP-table levels and gave identical inputs to ExProSE's readers.

The direct M2 smoke test reached its 540-second HHblits test deadline on the Windows-mounted database. A separate check replayed the previously verified HHM for the exact query and completed the other native stages. All 499 descriptors, the ordered 727-feature matrix, raw predictions, and the seven membrane calls and flags matched the frozen M2 result. A new complete search on this branch remains unverified.

## Licences and redistribution

DREAMM is [GPL-3.0](LICENSE). Review the [third-party notices](THIRD_PARTY_NOTICES.md), including the separate acquisition requirements and unresolved ProtDCal publication scope. Distribute the matching source with the CLI when publication is authorised.
