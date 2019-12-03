import matlab

trimmed_regions = {
    "pm3": [0.07, 0.28],
    "pm4": [0.33, 0.45],
    "pm5": [0.53, 0.70],
    "pm6": [0.80, 0.86],
    "pm7": [0.88, 0.96],
}
untrimmed_regions = {
    "pm3": [0.12, 0.30],
    "pm4": [0.355, 0.455],
    "pm5": [0.52, 0.65],
    "pm6": [0.73, 0.78],
    "pm7": [0.79, 0.85],
}
untrimmed_regions_with_medial = {
    "pm3": [0.12, 0.30],
    "pm4": [0.35, 0.44],
    "pm5": [0.52, 0.65],
    "pm6": [0.73, 0.78],
    "pm7": [0.79, 0.85],
    "medial_axis": [0.12, 0.85],
}

trimmed_regions_with_medial = {
    "pm3": [0.07, 0.28],
    "pm4": [0.33, 0.45],
    "pm5": [0.53, 0.70],
    "pm6": [0.80, 0.86],
    "pm7": [0.88, 0.96],
    "medial_axis": [0.05, 0.95],
}

opt_trimmed_regions = {}

opt_untrimmed_regions = {
    "pm3": [0.28166667, 0.29833333],
    "pm4": [0.405, 0.42166667],
    "pm5": [0.52666667, 0.54333333],
    "pm6": [0.75333333, 0.77],
    "pm7": [0.81, 0.82666667],
}

# matlab_engine = matlab.engine.start_matlab()
