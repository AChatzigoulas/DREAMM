# DREAMM v1.1

DREAMM (Drugging pRotein mEmbrAne Machine learning Method) uses three-dimensional protein structures to predict which amino acid residues may penetrate a membrane. Its ensemble machine-learning classifier combines five models by majority vote. It was developed and assessed primarily on peripheral membrane proteins. Accepting inputs from other protein classes does not establish predictive accuracy for those classes.

DREAMM can also search for putative ligand-binding pockets near the predicted membrane interface. It predicts membrane-penetrating residues once on the reference structure, then uses P2Rank to find pockets in each analyzed conformation. It filters pockets by their proximity to those residues and groups the retained pockets by location into candidate sites. You can provide experimental or simulation-derived conformations, or generate conformations from a reference using ExProSE distance geometry.

## Install on Linux

On x86-64 Linux, clone the `dev` branch for v1.1. GitHub's default branch (`main`) contains the historical standalone version.

```sh
git clone --branch dev https://github.com/AChatzigoulas/DREAMM.git
cd DREAMM
./install.sh
export PATH="$HOME/.local/bin:$PATH"
dreamm --version
```

Choose **basic** for interface prediction (M1/M2) or **full** to also analyze pockets (M3–M5). Before installing, obtain the vendor MSMS 2.6.1 archive and prepare storage for UniClust30 2018_08. The installer can reuse the exact database or download it. See the [installation guide](INSTALL.md) for prerequisites and the tested platform.

## Choose a workflow

| Mode | Example | What it analyzes |
| --- | --- | --- |
| M1 | `dreamm run -i 1RLW -c A` | Membrane-penetrating residues from a PDB accession or a local PDB/mmCIF file. |
| M2 | `dreamm run --mode M2 -i protein.pdb` | The same residue prediction from a local PDB/mmCIF file. |
| M3 | `dreamm run --mode M3 -i 2KS4` | Interface pockets across the models of a deposited PDB entry. Single-model entries and local PDB/mmCIF files are also accepted. |
| M4 | `dreamm run --mode M4 -i ensemble.pdb` | Interface pockets in one or more conformations from a local PDB/mmCIF file. |
| M5 | `dreamm run --mode M5 -i 1RLW --additional 2` | Interface pockets in the reference and 1–50 additional ExProSE conformations. Accepts a PDB accession or a local PDB/mmCIF file. |

Use `-c A B` for selected author chains. Omitting `-c` analyzes all supported protein chains together. M1, M2 and the reference stage of M3–M5 use the first model. For an ensemble, put a biologically meaningful membrane-interacting conformation first where possible.

A completed analysis can return zero membrane-penetrating residues or zero interface sites. The [results guide](INSTALL.md#understand-your-results) explains the eligibility filter, terminal and gap flags, site rankings, and output files. DREAMM provides computational hypotheses for follow-up. It does not determine membrane orientation, lipid-specific or ligand-binding affinity, experimental druggability, or thermodynamic occupancy.

## Cite DREAMM

For membrane-penetrating residue prediction, cite:

- Alexios Chatzigoulas and Zoe Cournia (2022). **Predicting protein–membrane interfaces of peripheral membrane proteins using ensemble machine learning.** *Briefings in Bioinformatics*, 23(2), bbab518. [DOI 10.1093/bib/bbab518](https://doi.org/10.1093/bib/bbab518)

For the interface-pocket workflow, cite both the methodology paper above and:

- Alexios Chatzigoulas and Zoe Cournia (2022). **DREAMM: a web-based server for drugging protein-membrane interfaces as a novel workflow for targeted drug design.** *Bioinformatics*, 38(24), 5449–5451. [DOI 10.1093/bioinformatics/btac680](https://doi.org/10.1093/bioinformatics/btac680)

## Project information

- [GPL-3.0 licence](LICENSE) and [third-party terms](THIRD_PARTY_NOTICES.md)
- [Release notes](RELEASE_NOTES_v1.1.md) and [developer guide](DEVELOPERS.md)
- Historical [command-line README](https://github.com/AChatzigoulas/DREAMM/blob/481055593c5676fb8f2a29c13e776782d772e527/README.md) and [webserver manual](https://wiki.ni4os.eu/images/5/5c/DREAMM-User_manual.pdf)
- [Report an issue](https://github.com/AChatzigoulas/DREAMM/issues)
