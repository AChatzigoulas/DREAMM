# DREAMM v1.1

DREAMM predicts membrane-binding residues in peripheral membrane proteins. Ensemble modes use the membrane prediction of the reference structure to predict and rank potential binding sites.

## Install v1.1

From a Linux Git checkout, run

```sh
./install.sh
```

Choose basic for M1/M2 or full for M1–M5. Both profiles are Chimera-free for supported inputs. See the [Linux installation guide](INSTALL.md) for the separately obtained MSMS archive, UniClust30 storage, and platform requirements.

## Run DREAMM

```sh
dreamm run -i 1RLW -c A                       # M1, PDB ID and optional author chains
dreamm run --mode M2 -i protein.pdb           # First model of a local structure
dreamm run --mode M3 -i 2KS4                  # Models from a deposited NMR ensemble
dreamm run --mode M4 -i ensemble.pdb          # Local structure or ensemble
dreamm run --mode M5 -i 1RLW --additional 2    # Reference plus two generated frames
```

An omitted chain selector uses all admitted protein chains. Use `-c A B` for selected chains. Predictions are computational. The [validation scope](INSTALL.md#validation-scope) records the tested platforms and input paths. The [developer guide](DEVELOPERS.md) maps the original scientific code to the standalone modules. Review the [third-party terms](THIRD_PARTY_NOTICES.md) before obtaining external prerequisites or preparing a distribution.

## License
Distributed under the GPL-3.0 License. See `LICENSE` for more information.


## Issues
For any bugs or questions on usage feel free to use the issue tracker of this github repo.

## Citing DREAMM

If you use DREAMM in your publication please cite the following publication.

Alexios Chatzigoulas and Zoe Cournia.
**Predicting protein–membrane interfaces of peripheral membrane proteins using ensemble machine learning.**
*Briefings in Bioinformatics*, **2022**, bbab518.
[doi:10.1093/bib/bbab518](https://academic.oup.com/bib/advance-article/doi/10.1093/bib/bbab518/6527274)

For the DREAMM web server, also cite the following publication.

Alexios Chatzigoulas and Zoe Cournia.
**DREAMM: A web-based server for drugging protein-membrane interfaces as a novel workflow for targeted drug design.**
*Bioinformatics*, **2022**, btac680,
[doi:10.1093/bioinformatics/btac680](https://doi.org/10.1093/bioinformatics/btac680)

## Historical documentation

The original implementation and setup instructions are preserved in the [historical README](https://github.com/AChatzigoulas/DREAMM/blob/481055593c5676fb8f2a29c13e776782d772e527/README.md). That guide describes the legacy release. For v1.1, use the [guided installer](INSTALL.md).
