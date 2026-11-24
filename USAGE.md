# Using DREAMM and interpreting results

## Choose the reference and chains

See the [five workflow examples](README.md#choose-a-workflow). M1/M3/M5 can fetch a four-character PDB accession over HTTPS from RCSB. All modes accept local PDB (`.pdb`, `.ent`) and mmCIF (`.cif`, `.mmcif`). A local file fixes the exact input coordinates and lets you supply a particular biological assembly. An accession retrieves the deposited entry.

Use `-c A B` or `-c A,B` to select author chains. Omitting `-c` keeps all supported protein chains together, including their interactions. M1/M2 classify only the first model. M3/M4 prepare and align the supplied models, including the first. M5 uses the first as the reference and adds the 1–50 conformations requested with `--additional`. ExProSE generates structures by distance geometry with tolerance weighting `-w 0.4`. Independent generations can differ.

For M3–M5, membrane prediction runs once on the reference. Where possible, choose a first model representative of the membrane-interacting state. Supply consistent selected polymer identities across models and use `MODEL`/`ENDMDL` records for multi-model PDB. A single model is accepted by M3/M4 and provides no evidence of recurrence across conformations.

Preparation records conversions of supported modified amino acids. Ambiguous identities, unsupported chemistry or identifiers that downstream PDB tools cannot represent can stop a run. In particular, ExProSE cannot preserve insertion codes, and M5 hydrogen preparation needs supported bonded topology. For an AlphaFold input, review local pLDDT near the proposed interface and consider whether poorly supported regions belong in the analysis. DREAMM does not remove them automatically. [Original workflow paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC9750117/)

## Find the outputs

The CLI prints the path of a new `DREAMM-<accession-or-mode>-<suffix>/` directory in your working directory. To name it yourself, add `--output DIRECTORY` with a new directory name. Paths below are relative to this run directory.

| Analysis | Start here | Other outputs |
| --- | --- | --- |
| M1/M2 | `exports/membrane.csv` lists the final calls. | `exports/DREAMM-results.zip` includes that table, the prepared `structure.pdb`, `view.pml` and the native evidence, including raw classifier labels, feature tables, scaling and residue mappings. Open the PyMOL script from its containing directory. |
| M3–M5 | Open `attempt/results/DREAMM-ensemble.zip` and read the profile's `results.txt` and `binding-sites.csv` or `.xlsx`. | Reference calls and Conservation, structures for each frame, raw P2Rank output, interface associations, PyMOL scripts and exact frame/pocket memberships in `profile-result.json`. M5 also includes generated coordinates and available generation analyses, including PCA where defined. |

A top-level `complete.json` marks a completed run. Zero final membrane calls can be a valid result when eligible residues were classified. An ensemble with no qualifying interface pockets has empty site tables and a zero-site summary. Frames without qualifying pockets need no ESSA calculation, and an empty association set needs no clustering. A stage error or interrupted run can leave partial files. A reference with **no eligible classifier rows** raises an analysis error in v1.1.

## Read the membrane calls

The classifier evaluates prepared residues with **mean residue depth < 2.5 Å**. Each eligible residue gets a raw label from a majority vote of five models. A positive label is a candidate. Final calls exclude negative residue numbers. The original filter retains a positive residue in its designated polar group (A, R, N, D, C, E, Q, G, H, S, K) only when its center of mass lies **< 14 Å** from that of a positive residue in the complementary group with a nonnegative residue number.

In `membrane.csv`, `chain` and `resnum` identify the residue, including an insertion suffix where supported. `Amino acid` is its prepared one-letter identity. A `broken_chain` value of 1 flags a call within three integer residue positions of a chain terminus or numbering gap. The call remains in the table. Use the source-to-prepared mapping for converted residues.

## Read interface pockets and ranked sites

P2Rank 2.5.1 predicts pockets independently in each aligned conformation. A pocket qualifies when the closest atom pair between a reference-predicted membrane residue in that frame and the pocket’s assigned surface atoms is **< 5 Å**. Exactly 5 Å fails the filter. `interface-pockets.csv` and `.xlsx` give the individual associations. `dist_surf` is the filtering distance, while `dist_center` is the minimum distance from that residue to the pocket center.

DREAMM clusters the retained pocket centers across conformations. It uses KMeans with silhouette-based cluster-count selection when a frame contributes multiple retained pockets, and MeanShift when every frame contributes at most one. Sites are ranked by their **highest P2Rank score**, in descending order. The following columns in `binding-sites.csv` and `.xlsx` describe different aspects of a site.

| Field | Interpretation |
| --- | --- |
| `highest_score`, `highest_score_conf` | Best P2Rank pocket score in the site and the corresponding conformation. The raw P2Rank tables also contain the predictor’s probability field. Neither establishes ligand affinity or experimental druggability. |
| `total_conformations` | Number of distinct analyzed frames containing the site. The denominator in `results.txt` includes every analyzed frame, including the reference. Occurrence measures recurrence in the supplied or generated ensemble. It cannot establish equilibrium occupancy. |
| `conservation_mean`, `conservation_std` | Conservation comes from the HHblits emission for each reference query residue. DREAMM computes a mean over each pocket’s P2Rank-assigned residues, then summarizes by frame and site. Fresh searches at the pinned settings can produce different values. |
| `ESSA_mean`, `ESSA_std` | Essential Site Scanning Analysis estimates how strongly local perturbations affect modeled global motions. DREAMM takes the median ESSA over a pocket’s assigned residues in its frame, then summarizes by frame and site. |
| `membrane-penetrating_residues`, center and distance statistics | Associated residues and spatial summaries. Use the `frames` and `pockets` lists in `profile-result.json` for exact membership, including multiple pockets in a frame. |

The default P2Rank model is `default`. Use `--ensemble-profile alphafold` to run the alternate model and record which profile you used. Neither profile establishes membrane orientation, lipid specificity or binding affinity. DREAMM was assessed primarily for peripheral membrane proteins. Its ability to read another class of structure does not establish predictive accuracy for that class.
