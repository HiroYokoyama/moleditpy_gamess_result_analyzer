"""The features the GAMESS analyzer claims, with the real output that shows each."""

# Every claimed window: (fixture, main-window slot, window class).
CLAIMS = [
    ("water_rhf_mo", "show_scf_trace", "SCFTraceDialog"),
    ("o2_uhf_opt", "show_scf_trace", "SCFTraceDialog"),
    ("water_rhf_mo", "show_mo_analyzer", "MODialog"),
    ("h2co_cct_mo", "show_mo_analyzer", "MODialog"),
    ("water_ccq_mo", "show_mo_analyzer", "MODialog"),
    ("o2_uhf_opt", "show_mo_analyzer", "MODialog"),
    ("water_c2v_mo", "show_mo_analyzer", "MODialog"),
    ("water_opt_freq", "show_trajectory", "TrajectoryResultDialog"),
    ("hcn_irc", "show_trajectory", "TrajectoryResultDialog"),
    ("water_drc", "show_trajectory", "TrajectoryResultDialog"),
    ("methylene_mex", "show_trajectory", "TrajectoryResultDialog"),
    ("water_casscf", "show_mo_analyzer", "MODialog"),
    ("methylene_gvb", "show_mo_analyzer", "MODialog"),
    ("water_dimer_fmo", "show_forces", "ForceViewerDialog"),
    ("water_transitn", "show_tddft", "TDDFTDialog"),
    ("water_gradient", "show_forces", "ForceViewerDialog"),
    ("water_opt_freq", "show_forces", "ForceViewerDialog"),
    ("water_rhf_mo", "show_charges", "ChargeDialog"),
    ("water_pdc", "show_charges", "ChargeDialog"),
    ("water_rhf_mo", "show_dipole", "DipoleDialog"),
    ("water_raman", "show_freq", "FrequencyDialog"),
    ("water_opt_freq", "show_freq", "FrequencyDialog"),
    ("water_opt_freq", "show_thermal", "ThermalTableDialog"),
    ("h2co_tddft", "show_tddft", "TDDFTDialog"),
    ("methanol_nmr", "show_nmr", "NMRDialog"),
    ("water_rhf_mo", "show_bond_analysis", "BondAnalysisDialog"),
    ("water_mp2", "show_energy_components", "EnergyComponentsDialog"),
    ("water_ccsdt", "show_energy_components", "EnergyComponentsDialog"),
    ("water_d3", "show_properties", "PropertiesDialog"),
    ("water_rhf_mo", "show_properties", "PropertiesDialog"),
]

# (fixture, slot, controls that must be there, controls that must not).
CONTROLS = [
    ("water_opt_freq", "show_trajectory", {"Play", "Save GIF"}, {"Load MEP TRJ", "Scan Points", "Coordinate"}),
    ("h2co_tddft", "show_tddft", {"Export Sticks (CSV)"}, {"CD", "Absorption"}),
    ("methanol_nmr", "show_nmr", {"Export CSV"}, {"Simulate Coupling"}),
    ("water_raman", "show_freq", {"Open IR/Raman Spectrum..."}, {"Show Dipole Change"}),
]

TABS = [
    ("water_rhf_mo", "show_bond_analysis", "Mayer", "NBO"),
    ("water_raman", "show_freq", "Mode Vectors", "Dipole"),
]

# (fixture, button attribute): a job that has the result enables the button.
BUTTONS = [
    ("water_rhf_mo", "btn_scf"),
    ("water_rhf_mo", "btn_mo"),
    ("water_opt_freq", "btn_traj"),
    ("hcn_irc", "btn_traj"),
    ("water_casscf", "btn_mo"),
    ("water_transitn", "btn_tddft"),
    ("water_gradient", "btn_forces"),
    ("water_rhf_mo", "btn_charge"),
    ("water_rhf_mo", "btn_dipole"),
    ("water_raman", "btn_freq"),
    ("water_opt_freq", "btn_therm"),
    ("h2co_tddft", "btn_tddft"),
    ("methanol_nmr", "btn_nmr"),
]

# (fixture, button attribute): a job without the result leaves it disabled.
DISABLED = [
    ("water_rhf_mo", "btn_freq"),
    ("water_rhf_mo", "btn_tddft"),
    ("water_rhf_mo", "btn_nmr"),
    ("water_rhf_mo", "btn_traj"),
    ("water_rhf_mo", "btn_forces"),
    ("water_rhf_mo", "btn_therm"),
    ("methanol_nmr_abnormal", "btn_mo"),
]
