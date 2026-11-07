import os

# New set of simplified, robust icons using explicit strokes and avoiding fill complexities.
# Using hardcoded color #333333 for maximum compatibility (dark grey).

ICONS = {
    # File Menu
    "menu_open.svg": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
<path d="M4 20h16a2 2 0 0 0 2-2V8h-5l-2-4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2z" stroke="#333333" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
</svg>""",
    "menu_reload.svg": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
<path d="M23 4v6h-6M1 20v-6h6" stroke="#333333" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
<path d="M20.49 15a9 9 0 0 1-17.12.63M3.51 9a9 9 0 0 1 17.12-.63" stroke="#333333" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
</svg>""",
    "menu_close.svg": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
<rect x="3" y="3" width="18" height="18" rx="2" ry="2" stroke="#333333" stroke-width="2"/>
<line x1="9" y1="9" x2="15" y2="15" stroke="#333333" stroke-width="2" stroke-linecap="round"/>
<line x1="15" y1="9" x2="9" y2="15" stroke="#333333" stroke-width="2" stroke-linecap="round"/>
</svg>""",
    # Analysis Buttons - Main Tools
    "icon_scf.svg": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
<path d="M3 21h18" stroke="#333333" stroke-width="2" stroke-linecap="round"/>
<!-- Downward graph with points -->
<path d="M3 5l6 10l6 3l6 1" stroke="#333333" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
<circle cx="3" cy="5" r="2" fill="#333333"/>
<circle cx="9" cy="15" r="2" fill="#333333"/>
<circle cx="15" cy="18" r="2" fill="#333333"/>
<circle cx="21" cy="19" r="2" fill="#333333"/>
</svg>""",
    "icon_mo.svg": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
<!-- Vertical lobes (Rotated 90) -->
<path d="M12 12c4-3 4-8 0-8s-4 5 0 8z" stroke="#333333" stroke-width="2"/>
<path d="M12 12c4 3 4 8 0 8s-4-5 0-8z" stroke="#333333" stroke-width="2"/>
<circle cx="12" cy="12" r="2" stroke="#333333" stroke-width="2"/>
</svg>""",
    "icon_traj.svg": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
<!-- Diagonal top-left to bottom-right (Rotated 90) -->
<path d="M5 5L19 19" stroke="#333333" stroke-width="2"/>
<circle cx="5" cy="5" r="2.5" fill="#333333"/>
<circle cx="12" cy="12" r="2.5" fill="#333333"/>
<circle cx="19" cy="19" r="2.5" fill="#333333"/>
</svg>""",
    "icon_forces.svg": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
<path d="M12 12m-3 0a3 3 0 1 0 6 0a3 3 0 1 0 -6 0" stroke="#333333" stroke-width="2"/>
<!-- Arrows pointing OUTWARDS (Expanding) -->
<!-- Right side: pointing right -->
<path d="M17 12h5m0 0l-2 -2m2 2l-2 2" stroke="#333333" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
<!-- Left side: pointing left -->
<path d="M7 12h-5m0 0l2 -2m-2 2l2 2" stroke="#333333" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
<!-- Top: pointing up -->
<path d="M12 7v-5m0 0l-2 2m2 -2l2 2" stroke="#333333" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
<!-- Bottom: pointing down -->
<path d="M12 17v5m0 0l-2 -2m2 2l 2 -2" stroke="#333333" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
</svg>""",
    # Properties
    "icon_charge.svg": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
<circle cx="12" cy="12" r="9" stroke="#333333" stroke-width="2"/>
<path d="M8 12h8M12 8v8" stroke="#333333" stroke-width="2" stroke-linecap="round"/>
</svg>""",
    "icon_dipole.svg": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
<!-- Arrow pointing right -->
<path d="M2 12h18m-4-4l4 4-4 4" stroke="#333333" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
<!-- Plus at origin (tail is at 2,12). Vertical bar at x=6 to make it a plus -->
<path d="M6 8v8" stroke="#333333" stroke-width="2" stroke-linecap="round"/>
</svg>""",
    # Vibrations / Thermo
    "icon_freq.svg": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
<path d="M4 12c0-8 8-8 8 0s8 8 8 0" stroke="#333333" stroke-width="2" stroke-linecap="round"/>
</svg>""",
    "icon_therm.svg": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
<!-- Fire / Flame Icon Revised -->
<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.1.2-2.2.6-3a7 7 0 0 1 2.9-6.5" stroke="#333333" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
</svg>""",
    # Spectroscopy
    "icon_tddft.svg": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
<!-- Multiple GAMESS Broadened Peaks -->
<path d="M2 18c2 0 3-10 6-10s4 10 6 10s3-6 5-6s3 6 3 6" stroke="#333333" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
<!-- Vertical Lines (Sticks) for peaks - Dashed and Thinner -->
<line x1="8" y1="8" x2="8" y2="18" stroke="#333333" stroke-width="1" stroke-dasharray="2 2"/>
<line x1="19" y1="12" x2="19" y2="18" stroke="#333333" stroke-width="1" stroke-dasharray="2 2"/>
</svg>""",
    "icon_nmr.svg": """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
<!-- Bar Chart Style (Enlarged) -->
<path d="M3 21h18M6 21v-12M10 21v-8M14 21v-18M18 21v-10" stroke="#333333" stroke-width="2" stroke-linecap="round"/>
</svg>""",
}

current_dir = os.path.dirname(os.path.abspath(__file__))
# Changin folder name to 'icon' as requested
img_dir = os.path.join(current_dir, "icon")

if not os.path.exists(img_dir):
    os.makedirs(img_dir)

for name, content in ICONS.items():
    file_path = os.path.join(img_dir, name)
    try:
        with open(file_path, "w") as f:
            f.write(content)
        print(f"Created: {name}")
    except Exception as e:
        print(f"Error writing {name}: {e}")
