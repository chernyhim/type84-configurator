import re

with open('scratch/layout-classic.js', 'r', encoding='utf-8', errors='ignore') as f:
    js = f.read()

# 1. Search for profile occurrences
matches = re.findall(r'["\']([^"\']{0,40}profile[^"\']{0,40})["\']', js, re.IGNORECASE)
print('Profile strings (unique):', len(set(matches)))
for s in sorted(set(matches))[:40]:
    print('  ', repr(s))

# 2. Search for GET_MAGNETIC_AXIS_RT / GET_MAGNETIC_AXIS_DKS_DATA / SET_MAGNETIC_AXIS_RT
print('\n=== Magnetic Axis / Hall functions ===')
for fn_name in ['Xo', 'Pu', 'Jo', 'Uu', 'Po', 'iu']:
    idx = js.find(f'{fn_name}=')
    if idx != -1:
        print(f'\n--- {fn_name} ---')
        print(js[idx:idx+400])
