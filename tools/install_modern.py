"""Install a pinned modern component profile offline; this does not enable M1."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from dreamm.modern import install_components

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--manifest', required=True)
parser.add_argument('--sha256', required=True)
parser.add_argument('--wheelhouse', required=True)
parser.add_argument('--output', required=True)
args = parser.parse_args()
print(install_components(args.manifest, args.sha256, args.wheelhouse, args.output))
