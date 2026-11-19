"""Freeze supported Julia/Chimera and the approved plotting-only ExProSE overlay."""
import argparse,difflib,hashlib,json,os,re,shutil,stat,tomllib
from pathlib import Path


def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True);a=p.parse_args();root=Path(a.root).absolute()
    if os.geteuid()==0:raise ValueError('non-root generation installation required')
    original=root/'depot/packages/ProteinEnsembles/TB23o';overlay=root/'exprose-pca-v1'
    manifest=tomllib.loads((root/'project/Manifest.toml').read_text())
    spec=manifest['deps']['ProteinEnsembles'][0]
    if spec['git-tree-sha1']!='0c88d70cf1a9170ed684c337c5117d41ae9f4b8f' or spec['version']!='0.3.1':raise ValueError('ExProSE source lineage differs')
    if any(p.is_symlink() for p in original.rglob('*')):raise ValueError('unexpected package source alias')
    if not overlay.exists():shutil.copytree(original,overlay)
    # Julia installs immutable package sources. Only the separate owned overlay
    # receives owner-write permission; the original depot stays unchanged.
    if overlay.is_symlink():raise ValueError('linked overlay rejected')
    overlay.chmod(0o700)
    for item in overlay.rglob('*'):
        if item.is_symlink():raise ValueError('linked overlay member rejected')
        item.chmod(0o700 if item.is_dir() or item.stat().st_mode&0o111 else 0o600)
    path=overlay/'src/plot.jl';old=path.read_text(encoding='utf-8')
    before='''                    comps_to_plot::Array{Tuple{Int,Int},1}=[(1,2), (1,3), (2,3)])
    checkfilepath(out_prefix)
    for (i, j) in comps_to_plot
        out_filepath = "$(out_prefix)_$(i)_$j.png"
        @assert length(pcs) > 0 "No projections in array"
'''
    after='''                    comps_to_plot::Array{Tuple{Int,Int},1}=
                        Tuple{Int,Int}[(i,j) for (i,j) in ((1,2), (1,3), (2,3)) if j <= size(pcs, 1)])
    @assert !isempty(pcs) "No projections in array"
    checkfilepath(out_prefix)
    if isempty(comps_to_plot)
        println("No two-dimensional PCA plot available")
        return nothing
    end
    for (i, j) in comps_to_plot
        out_filepath = "$(out_prefix)_$(i)_$j.png"
'''
    if old.count(before)!=1:raise ValueError('approved plotting context differs; original source retained')
    new=old.replace(before,after);path.write_text(new,encoding='utf-8')
    patch=''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='ProteinEnsembles-0.3.1/src/plot.jl',tofile='dreamm-pca-v1/src/plot.jl'))
    (root/'dreamm-pca-v1.patch').write_text(patch,encoding='utf-8')
    project=root/'patched-project';project.mkdir(mode=0o700);shutil.copyfile(root/'project/Project.toml',project/'Project.toml')
    text=(root/'project/Manifest.toml').read_text()
    pattern=r'(\[\[deps\.ProteinEnsembles\]\]\n)(.*?)(?=\n\[\[deps\.|\Z)'
    def modify(m):
        body=re.sub(r'^git-tree-sha1 = .*\n','',m[2],flags=re.M)
        return m[1]+'path = "../exprose-pca-v1"\n'+body
    text,n=re.subn(pattern,modify,text,flags=re.S)
    if n!=1:raise ValueError('one ExProSE dependency required')
    check=tomllib.loads(text)
    for name,entry in manifest['deps'].items():
        if name!='ProteinEnsembles' and check['deps'][name]!=entry:raise ValueError('unrelated dependency changed')
    (project/'Manifest.toml').write_text(text,encoding='utf-8')
    files={};links={}
    for path in root.rglob('*'):
        name=path.relative_to(root).as_posix()
        if path.is_symlink():
            resolved=path.resolve(strict=True)
            if not resolved.is_relative_to(root) or not resolved.is_file():raise ValueError('escaping or unresolved generation alias: '+name)
            links[name]=os.readlink(path)
        elif path.is_file():files[name]={'bytes':path.stat().st_size,'sha256':sha(path)}
        elif not path.is_dir():raise ValueError('special runtime file')
    if len(files)+len(links)>60000:raise ValueError('runtime inventory bound')
    result={'schema':'dreamm-generation-runtime/1','julia':'1.10.13','ProteinEnsembles':'0.3.1','chimera':'1.19_b42556' if (root/'chimera/bin/chimera').is_file() else None,
      'original_exprose_tree':'0c88d70cf1a9170ed684c337c5117d41ae9f4b8f',
      'pca_patch_sha256':hashlib.sha256(patch.encode()).hexdigest(),'files':files,'links':links,
      'random_seed_supplied':False,'RNG':'Julia1.10.TaskLocalRNG','weight':'0.4',
      'plotting_only_correction':True,'M1_scientific_changes':False}
    raw=json.dumps(result,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()+b'\n'
    if len(raw)>8*1024**2:raise ValueError('runtime receipt bound')
    with (root/'.runtime.pending').open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    os.rename(root/'.runtime.pending',root/'runtime.json')
    print(json.dumps({'runtime_sha256':hashlib.sha256(raw).hexdigest(),'files':len(files),'aliases':len(links),
                     'original_package_unmodified':sha(original/'src/plot.jl')==hashlib.sha256(old.encode()).hexdigest()}))


if __name__=='__main__':main()
