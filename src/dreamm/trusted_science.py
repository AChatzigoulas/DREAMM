"""Fixed scientific settings for trusted-local execution on variable CPU hosts.

Only resource discovery differs from the historical host-specific CPU heuristic.
Query construction and HHM conversion are the immutable scientific functions.
"""
from dreamm_m1 import native_m1 as native
from dreamm_m1.capture import _read
from dreamm_m1.identity import canonical
from dreamm_m1.measurement import measured


@measured('features.native_hhblits')
def conservation(work, dssp, bindings, seconds):
    sequence = ''.join(row['Amino acid'] for row in dssp.values())
    fasta, query = native.query_input(sequence, [bindings[loc].uid for loc in dssp])
    (work/'input.fasta').write_bytes(fasta)
    (work/'query.json').write_bytes(canonical(query))
    argv = (native.HHBLITS, '-i', str(work/'input.fasta'), '-d', native.DATABASE,
            '-cpu', '15', '-M', 'first', '-n', '3', '-ohhm', str(work/'output.hhm'), '-v', '0')
    native._tool(argv,work,'hhblits',seconds)
    values = native.conservation_scores(_read(work/'output.hhm'),sequence)
    (work/'conservation.json').write_bytes(canonical([
        {'source_residue_uid':bindings[loc].uid,'value':value} for loc,value in zip(dssp,values)]))
    return dict(zip(dssp,values))
