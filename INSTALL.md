# Install and run DREAMM v1.1

Use x86-64 Linux and a regular user account. The tested platform is Ubuntu 22.04 under WSL. Rocky Linux 9 and other distributions have yet to be qualified.

1. Install Python 3.9 or later, Git, GCC/G++, make, tar and system CA certificates from your distribution. On Ubuntu 22.04, use `python3 git build-essential tar ca-certificates`. For the **full** installation also install `fontconfig fonts-dejavu-core`. The host Python starts installation. DREAMM installs and uses its own pinned Python 3.14.10.
2. Obtain [vendor MSMS 2.6.1](https://github.com/sannerlab/downloads/releases/tag/msms-2.6.1) under the [applicable terms](THIRD_PARTY_NOTICES.md). Keep the filename `msms_i86_64Linux2_2.6.1.tar.gz` and put it in your Linux home directory's `Downloads/`, or enter its path when prompted.
3. Have an existing **UniClust30 2018_08 HH-suite** database ready, or choose a local download volume during setup. The database uses 86.4 GiB unpacked. Allow about 140 GiB on the database volume during download and extraction. The installer also checks for at least 6 GiB free on the runtime volume. Allow additional RAM and disk space for scientific jobs. Prefer an SSD and a native Linux filesystem for the database. A Windows-mounted WSL drive can make database access much slower.

```sh
git clone --branch dev https://github.com/AChatzigoulas/DREAMM.git
cd DREAMM
./install.sh
export PATH="$HOME/.local/bin:$PATH"
dreamm run -i 1RLW -c A
```

Choose **basic** (M1/M2) or **full** (M1–M5). The installer obtains and verifies the pinned dependencies, builds FreeSASA and ProDy locally, and finds the original model, scaler input and ProtDCal assets in the Git checkout. Full setup also installs P2Rank, Excel export support, Julia and the pinned ExProSE environment. Initial setup needs network access unless the required downloads are already available. No Conda environment is required.

The default installation directory is `~/.local/share/dreamm` and the launcher is `~/.local/bin/dreamm`. The PATH line above applies to the current shell. Add it to your shell startup file if needed. To add ensemble support to an existing basic installation, run `./install.sh --profile full`.

## Prepare the input

See the [mode examples](README.md#choose-a-workflow). Local coordinates can be supplied as `.pdb`, `.ent`, `.cif` or `.mmcif`. M1, M3 and M5 also accept four-character PDB accessions and fetch the deposited mmCIF over HTTPS. Use a saved local file to repeat an analysis on the exact same source bytes. Accession input uses the deposited coordinates. Supply a local file if you need a specific biological assembly.

Select author chain IDs with `-c A B` or `-c A,B`. Omitting the selector keeps all supported protein chains together. For ensembles, supply the same selected protein residue identities in every model. Use standard `MODEL`/`ENDMDL` records for multi-model PDB files. M3 and M4 can analyze one model, but a single model cannot establish recurrence across conformations. For M5, `--additional N` requests 1–50 generated models in addition to the reference. ExProSE uses distance geometry with tolerance weighting `-w 0.4`. It does not run molecular dynamics, and separately generated ensembles can differ.

For structure preparation, DREAMM uses the original conversion table for supported modified amino acids and records identity changes. It can reject unsupported chemistry, ambiguous identity, or coordinates and identifiers that cannot be represented in the PDB files required by downstream tools. For example, the ExProSE reader does not preserve insertion codes. M5 also requires supported bonded topology for hydrogen preparation. Submitting an AlphaFold model does not filter low-confidence regions automatically. Review pLDDT and preprocess poorly supported, unstructured regions when scientifically justified, especially near the proposed interface. Consult the [original workflow paper](https://doi.org/10.1093/bioinformatics/btac680) for its AlphaFold input guidance.

## Understand your results

Results are written to a new `DREAMM-<ID-or-mode>-<suffix>/` directory in the current working directory. Use `--output DIRECTORY` to choose a different new directory. The CLI prints the output paths. A finished run writes `complete.json`. Logs and partial files can remain after a failure. Use the completion record to distinguish a valid empty result from an interrupted or failed calculation.

### Membrane-penetrating residues

The classifier evaluates residues with **mean residue depth strictly below 2.5 Å** in the prepared reference structure. Residues outside that set have no raw classifier label. Label 1 is a positive majority vote before filtering. Final calls exclude negative residue numbers. DREAMM retains a positive call in its original polar group (A, R, N, D, C, E, Q, G, H, S, K) only if its center of mass is **less than 14 Å** from at least one positive call in the complementary group, with a nonnegative residue number.

In `membrane.csv`, `chain` and `resnum` identify the residue, including an insertion suffix where supported. `Amino acid` is the prepared one-letter identity. `broken_chain=1` flags a call if any of the three integer residue positions on either side is absent from that chain's prepared residue inventory. It flags proximity to a terminus or numbering gap and leaves the call in the result. For runs with eligible residues, zero retained calls is a valid result. No eligible rows produces a separate analysis error in v1.1.

M1/M2 write `exports/DREAMM-results.zip`. The ZIP contains `membrane.csv`, the prepared `structure.pdb`, `view.pml` and the native evidence under `evidence/`, including raw labels, unscaled and scaled feature evidence, and residue mappings. Open the PyMOL script from its containing directory to highlight the final calls. Use the source provenance and mapping files to relate exported identities to the deposited input.

### Interface pockets and sites

P2Rank 2.5.1 predicts pockets separately in each prepared, aligned frame. A pocket is associated with a predicted membrane-penetrating residue when the shortest distance between an atom of that residue and a P2Rank-assigned pocket surface atom is **strictly less than 5 Å**. A distance of exactly 5 Å does not qualify. The separately reported distance to the pocket center does not enter this filter.

DREAMM clusters retained pocket centers across frames. It uses KMeans with silhouette-based cluster-count selection when a frame contributes multiple retained pockets, and MeanShift when at most one retained pocket occurs per frame. Sites are ordered by **highest P2Rank score within the site**, descending. The score, number of frames, Conservation and ESSA are separate measures. Read occurrence as the number of distinct analyzed frames with the site over all analyzed frames, including the reference. It describes recurrence in that input set and is not an equilibrium occupancy or a binding probability.

- The model's pocket score orders proposals within a frame. Raw P2Rank tables also contain a predictor probability field. Neither measures ligand affinity or establishes experimental binding. Use `--ensemble-profile alphafold` to select the alternate P2Rank model for a separate run. The CLI defaults to `default`; compare results within the same profile.
- **Conservation** is a sequence-profile value derived from the HHblits emission for each query residue on the reference. Each pocket is summarized by the mean over its P2Rank-assigned residues. A new HHblits search with the pinned settings can give different values.
- **ESSA** (Essential Site Scanning Analysis) estimates how strongly local perturbations affect modeled global protein motions. DREAMM takes the median ESSA over the P2Rank-assigned residues of each retained pocket in its frame. Site summaries aggregate the per-frame metrics. An ESSA value can guide hypotheses about functional motions and does not establish allostery experimentally.

M3–M5 write `attempt/results/DREAMM-ensemble.zip`. The archive includes the reference membrane CSV and Conservation, source/prepared/aligned frames, per-frame P2Rank pocket and residue CSVs, interface associations and ranked-site tables in CSV and XLSX, a text summary, PyMOL scripts and supporting coordinate files. The profile's `profile-result.json` lists exact frame and pocket memberships. M5 also includes the generated structures and available generation analysis such as RMSD, scores and, where defined, PCA output. If no pockets meet the interface rule, the completed ZIP contains empty site tables and a zero-site summary. ESSA and clustering are skipped where there are no interface pockets to analyze.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| `install.sh` is missing | Run `git branch --show-current` and use `dev` for v1.1. Use the branch-clone command above. |
| MSMS is missing or fails verification | Use the original vendor archive under its original filename. A different 2.6.1 build will fail the fingerprint check. |
| The database fails verification | Point to the directory containing the exact 2018_08 files. If it is incomplete or from a different release, choose a download volume with sufficient space. |
| Original model or ProtDCal assets cannot be read | Provide a local Git checkout that contains the original commit and the verified assets. A source ZIP and a shallow clone can omit the required Git objects. See below. |
| `dreamm` is not found | Add `~/.local/bin` to PATH, or use the path printed by the installer. A custom prefix puts the launcher in that prefix's `bin/`. |
| A job fails during preparation, generation or prediction | Read the stage error and retained logs. Verify selected chains, model correspondence, input chemistry and available RAM/disk before retrying. Set an appropriate job deadline for large structures or ensembles. |

## Advanced installation and runs

For noninteractive installation, specify the profile and the external asset paths:

```sh
./install.sh --profile full --database /data/uniclust30_2018_08 \
  --msms-archive ~/Downloads/msms_i86_64Linux2_2.6.1.tar.gz
```

Use `--database-volume DIR` instead of `--database` to download. A verified database receipt can be reused while the installation and database file metadata match. Use `dreamm run --full-database-verify` with the normal run arguments to repeat the full database hash check. Installation can recheck and reuse locked archives supplied with `--reuse-downloads DIR`.

The normal Git clone contains the original model, training reference and historical ProtDCal files and the commit from which installation reads them. A curated source archive built by `tools/build_release.py` omits those data and JARs. To install from that archive (or from a clone missing the original commit), pass `--asset-repo DIR` pointing to a lawfully held Git checkout containing those exact objects. A CLI zipapp by itself also needs an installed runtime and the database. See the [third-party distribution inventory](THIRD_PARTY_NOTICES.md).

The default overall deadline is five hours. Use `--timeout SECONDS` and `--stage-timeout STAGE=SECONDS` for relevant stages such as `hhblits`, `generation`, `p2rank` or `essa`. `--processes N` limits CPU affinity without changing the scientific thread settings, including the 15 HHblits threads. Use `--memory-mib`, `--scratch-mib`, `--source-mib` or `--artifact-mib` to set local preferences. Memory and scratch use are sampled. This path is for trusted local research input.

The CLI computes the ensemble reference once unless you supply a verified reference checkpoint. Use `dreamm export-reference --help` and `dreamm export-generation --help` for reuse. Checkpoints are bound to the input, chain selection and relevant runtime or generation settings. See the [developer guide](DEVELOPERS.md) for implementation details and deployment boundaries.

## Validation scope

The Ubuntu 22.04 WSL evidence includes fresh basic and full-profile installations, M1/M2 reference predictions, 20-frame 2KS4 M3/M4, a one-frame M4, and 1RLW M5 generation and analysis with two and three total frames. Some tests reused verified reference or stage outputs. An earlier full M2 test used two chains with duplicate residue numbers and an insertion at B:11A. These software checks do not establish predictive accuracy for additional protein classes, every topology, or other Linux distributions.

A focused 1RLW M2 check ran a fresh HHblits search on the verified database on ext4 and recomputed Conservation, reusing the other feature artifacts. All 95 eligible raw labels and all seven final calls and flags matched the earlier result. Conservation differed at 122 of 126 query positions (maximum absolute difference 0.00451). The new HHblits result therefore does not imply byte-identical feature matrices across searches.
