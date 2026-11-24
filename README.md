# DREAMM v1.1

DREAMM (Drugging pRotein mEmbrAne Machine learning Method) predicts which amino acid residues in a protein structure may penetrate a membrane. Its five-model classifier was developed and evaluated primarily on peripheral membrane proteins.

DREAMM can also identify putative ligand-binding pockets near that interface. It predicts the membrane residues once on the reference conformation, finds pockets with P2Rank in each conformation and groups nearby interface pockets into ranked sites.

## Choose an installation

| If you want to… | Install |
| --- | --- |
| Predict membrane-penetrating residues in a reference structure (M1/M2) | **Basic**. Requires MSMS 2.6.1 and UniClust30 2018_08. |
| Also find interface pockets in supplied conformations (M3/M4) or generate conformations with ExProSE (M5) | **Full**. Includes basic plus P2Rank, Excel export support and Julia/ExProSE, even if you only use supplied conformations. |

Both bundles use the same membrane predictor. Download the Linux x86-64 MSMS 2.6.1 archive from the [official vendor release](https://github.com/sannerlab/downloads/releases/tag/msms-2.6.1) under its licence. The [Linux installation guide](INSTALL.md) explains where to save it and how much space to allow for UniClust30. The v1.1 development branch is `dev` (`main` contains the historical version).

```sh
git clone --branch dev https://github.com/AChatzigoulas/DREAMM.git
cd DREAMM
./install.sh
export PATH="$HOME/.local/bin:$PATH"
```

## Choose a workflow

| Mode | Example | What it analyzes |
| --- | --- | --- |
| M1 | `dreamm run -i 1RLW -c A` | Membrane-penetrating residues from a PDB accession or a local PDB/mmCIF file. |
| M2 | `dreamm run --mode M2 -i examples/1rlw/source.pdb` | The same prediction from a local PDB/mmCIF file. This example file is included in the checkout. |
| M3 | `dreamm run --mode M3 -i 2KS4` | Interface pockets across the models of a deposited PDB entry. Single-model entries and local PDB/mmCIF files are also accepted. |
| M4 | `dreamm run --mode M4 -i examples/1rlw/source.cif` | The same analysis from a local file. This example has one model. Supply your own multi-model PDB/mmCIF for an experimental or simulation-derived ensemble. |
| M5 | `dreamm run --mode M5 -i 1RLW --additional 2` | Interface pockets in the reference and 1–50 additional ExProSE conformations. Accepts a PDB accession or a local PDB/mmCIF file. |

Use `-c A B` for selected author chains. Omitting `-c` analyzes all supported protein chains together. M1, M2 and the reference stage of M3–M5 use the first model. For an ensemble, put a biologically meaningful membrane-interacting conformation first where possible.

The command prints the results directory. Start with `exports/membrane.csv` for M1/M2 or the ranked binding sites in `attempt/results/DREAMM-ensemble.zip` for M3–M5. A completed analysis can return zero calls or sites. See the [input and results guide](USAGE.md) for flags, scores, occurrence and the limits of interpreting a prediction.

## Cite DREAMM

For membrane-penetrating residue prediction, cite:

- Alexios Chatzigoulas and Zoe Cournia (2022). **Predicting protein–membrane interfaces of peripheral membrane proteins using ensemble machine learning.** *Briefings in Bioinformatics*, 23(2), bbab518. DOI 10.1093/bib/bbab518. [Read the paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC8921665/)

For the interface-pocket workflow, cite both the methodology paper above and:

- Alexios Chatzigoulas and Zoe Cournia (2022). **DREAMM: a web-based server for drugging protein-membrane interfaces as a novel workflow for targeted drug design.** *Bioinformatics*, 38(24), 5449–5451. DOI 10.1093/bioinformatics/btac680. [Read the paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC9750117/)

## Project information

- [GPL-3.0 licence](LICENSE) and [third-party terms](THIRD_PARTY_NOTICES.md)
- [Release notes](RELEASE_NOTES_v1.1.md) and [developer guide](DEVELOPERS.md)
- [Historical command-line documentation](https://github.com/AChatzigoulas/DREAMM/blob/481055593c5676fb8f2a29c13e776782d772e527/README.md)
- [Report an issue](https://github.com/AChatzigoulas/DREAMM/issues)
