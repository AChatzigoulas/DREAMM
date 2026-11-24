# DREAMM v1.1

- Install v1.1 from the `dev` branch using one guided Linux installer. Basic covers membrane-residue prediction. Full adds pocket analysis and ExProSE generation.
- Run M1–M5 with a PDB accession or a local PDB/mmCIF file as appropriate for the mode. Select one or more author chains, or analyze all supported protein chains together.
- The original classifier, 727-column feature order, HHblits settings and 470 + 29 ProtDCal descriptor path are preserved. The original 165 residue conversions and chain-aware mapping keep source identities traceable.
- Pocket workflows reuse one membrane prediction for the reference and use P2Rank 2.5.1 on each frame. M5 analyzes the reference and 1–50 additional generated conformations. P2Rank 2.5.1 uses different models from historical DREAMM pocket predictions.
- Results include membrane calls, structures and PyMOL files. Ensemble analyses add per-frame pocket predictions, interface and ranked-site tables, and Excel exports. Completed zero-site results are distinct from failures.

See the [installation guide](INSTALL.md), the [input and results guide](USAGE.md) and the [third-party notices](THIRD_PARTY_NOTICES.md).
