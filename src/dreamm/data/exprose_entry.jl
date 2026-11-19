using ProteinEnsembles, Random

const PE = ProteinEnsembles
args = PE.parsecommandlinerun()
seed = get(ENV, "DREAMM_EXPROSE_SEED", "")
isempty(seed) || Random.seed!(parse(UInt64, seed))

if args["n_strucs"] == 1
    # Upstream runanalysis rejects singleton self-alignment, PCA and RMSF.
    # Use the same interaction and generation calls with their original defaults.
    @assert args["i2"] === nothing && args["d2"] === nothing
    @assert args["n_mods"] == 0 && args["mod_path"] === nothing && isempty(args["extra_pdbs"])
    output = args["out_dir"]
    PE.makedirectories(output, ["pdbs"])
    constraints = PE.interactions(args["i1"], args["d1"],
        other_ratio=args["other_ratio"], tolerance_weight=args["tolerance_weight"])
    ensemble = PE.generateensemble(constraints, 1)
    @assert length(ensemble.strucs) == 1
    PE.alignensemble!(ensemble, PE.atomcoords(constraints.atoms))
    prefix = PE.defaults["out_prefix"]
    PE.writeensemble("$output/pdbs/$prefix.pdb", ensemble)
    PE.writeensemblescores("$output/spe_scores.tsv", ensemble)
    PE.writefloatarray("$output/rmsds.tsv", PE.ensemblermsds(ensemble, constraints.atoms))
else
    PE.runfromshell(args)
end
