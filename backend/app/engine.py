def power(subsystem, weather, model="pr"):
    gti = max(0.0, float(weather["gti"]))
    if model == "pr":
        raw = subsystem.ac_capacity_kw * gti / 1000 * subsystem.performance_ratio
    else:
        cell = float(weather["air_temp"]) + (subsystem.noct_c - 20) * gti / 800
        factor = max(0.0, 1 + subsystem.temp_coefficient * (cell - 25))
        dc_kw = subsystem.module_wattage * subsystem.module_count / 1000 * gti / 1000 * factor
        # PR includes the supplied reference inverter loss (98.7%). Replace that
        # component with the configured efficiency instead of stacking losses twice.
        raw = dc_kw * subsystem.performance_ratio * (subsystem.inverter_efficiency / 0.987)
    return min(subsystem.ac_capacity_kw, max(0.0, raw))

def energy(power_kw, seconds):
    return power_kw * seconds / 3600
