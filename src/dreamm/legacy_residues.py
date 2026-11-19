"""Effective DREAMM residue conversions from dreamm/ML/extract_features.py.

Original scientific rules and attribution retained under GPL-3.0.
"""

LEGACY_RESIDUES = {
        'AR0':'ARG', 'ASH':'ASP', 'CYM':'CYS', 'CYX':'CYS', 'GLH':'GLU', 'HIE':'HIS', 'HSE':'HIS', 'HID':'HIS', 'HSD':'HIS', 'HIP':'HIS', 'DSG':'ASN',
        'HSP':'HIS', 'LYN':'LYS', 'TYM':'TYR', '2AS':'ASP', '3AH':'HIS', '5HP':'GLU', 'ACL':'ARG', 'AGM':'ARG', 'AIB':'ALA', 'ALM':'ALA', 'TRO':'TRP',
        'ALO':'THR', 'ALY':'LYS', 'ARM':'ARG', 'ASA':'ASP', 'ASB':'ASP', 'ASK':'ASP', 'ASL':'ASP', 'ASQ':'ASP', 'AYA':'ALA', 'BCS':'CYS', 'TRG':'LYS',
        'BHD':'ASP', 'BMT':'THR', 'BNN':'ALA', 'BUC':'CYS', 'BUG':'LEU', 'C5C':'CYS', 'C6C':'CYS', 'CAS':'CYS', 'CCS':'CYS', 'CEA':'CYS', 'TPQ':'ALA',
        'CGU':'GLU', 'CHG':'ALA', 'CLE':'LEU', 'CME':'CYS', 'CSD':'ALA', 'CSO':'CYS', 'CSP':'CYS', 'CSS':'CYS', 'CSW':'CYS', 'CSX':'CYS', 'TPO':'THR',
        'CXM':'MET', 'CY1':'CYS', 'CY3':'CYS', 'CYG':'CYS', 'MEN':'ASN', 'TYB':'TYR', 'TYI':'TYR', 'TYQ':'TYR', 'TYS':'TYR', 'TYY':'TYR', 'TPL':'TRP',
        'CYM':'CYS', 'CYQ':'CYS', 'DAH':'PHE', 'DAL':'ALA', 'DAR':'ARG', 'DAS':'ASP', 'DCY':'CYS', 'DGL':'GLU', 'DGN':'GLN', 'DHA':'ALA', 'TIH':'ALA',
        'DHI':'HIS', 'DIL':'ILE', 'DIV':'VAL', 'DLE':'LEU', 'DLY':'LYS', 'DNP':'ALA', 'DPN':'PHE', 'DPR':'PRO', 'DSN':'SER', 'DSP':'ASP', 'SVA':'SER',
        'DTH':'THR', 'DTR':'TRP', 'DTY':'TYR', 'DVA':'VAL', 'EFC':'CYS', 'FLA':'ALA', 'FME':'MET', 'GGL':'GLU', 'GL3':'GLY', 'GLZ':'GLY', 'STY':'TYR',
        'GMA':'GLU', 'GSC':'GLY', 'HAC':'ALA', 'HAR':'ARG', 'HIC':'HIS', 'HIP':'HIS', 'HMR':'ARG', 'HPQ':'PHE', 'HTR':'TRP', 'HYP':'PRO', 'SOC':'CYS',
        'IAS':'ASP', 'IIL':'ILE', 'IYR':'TYR', 'KCX':'LYS', 'LLP':'LYS', 'LLY':'LYS', 'LTR':'TRP', 'LYM':'LYS', 'LYZ':'LYS', 'MAA':'ALA', 'SMC':'CYS',
        'MHS':'HIS', 'MIS':'SER', 'MLE':'LEU', 'MPQ':'GLY', 'MSA':'GLY', 'MSE':'MET', 'MVA':'VAL', 'NEM':'HIS', 'NEP':'HIS', 'NLE':'LEU', 'SHR':'LYS',
        'NLN':'LEU', 'NLP':'LEU', 'NMC':'GLY', 'OAS':'SER', 'OCS':'CYS', 'OMT':'MET', 'PAQ':'TYR', 'PCA':'GLU', 'PEC':'CYS', 'PHI':'PHE', 'SHC':'CYS',
        'PHL':'PHE', 'PR3':'CYS', 'PRR':'ALA', 'PTR':'TYR', 'PYX':'CYS', 'SAC':'SER', 'SAR':'GLY', 'SCH':'CYS', 'SCS':'CYS', 'SCY':'CYS', 'SET':'SER',
        'SEL':'SER', 'SEP':'SER', 'DSG':'ASN', 'MED':'MET', '2TL':'THR', 'CYSF':'CYS', 'MLY':'LYS', 'CAF':'CYS', 'YCM':'CYS', 'ALS':'ALA', 'PPN':'PHE',
        'SEC':'CYS', 'CYSP':'CYS', 'LSN':'LYS'
        }
