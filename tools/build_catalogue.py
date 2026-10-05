"""Regenerate the bundled facility template's instrument files.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

The catalogue below is the single source for
``khervelab/data/facility_template/instruments/*.yaml``. Instruments with
``source: materials-website`` are the ones the Imperial College Department
of Materials lists on its facility pages (checked October 2026); the
``added`` ones are common materials-characterisation kit a facility is
likely to want to book as well. Edit here, then run::

    python tools/build_catalogue.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from khervelab.core.yamlio import dump_yaml  # noqa: E402

OUT = ROOT / "khervelab" / "data" / "facility_template" / "instruments"

WEEKDAYS = {d: ["08:00-20:00"] for d in ("mon", "tue", "wed", "thu", "fri")}
ALL_WEEK = {d: ["00:00-24:00"] for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
OFFICE = {d: ["09:00-17:30"] for d in ("mon", "tue", "wed", "thu", "fri")}

# Category defaults: (colour, bookable hours, granularity, min, max minutes,
# max advance days, max concurrent per user)
CATEGORIES = {
    "Photoelectron spectroscopy": ("#1f6feb", ALL_WEEK, 30, 60, 24 * 60, 60, 3),
    "Scanning electron microscopy": ("#2da44e", WEEKDAYS, 30, 30, 8 * 60, 28, 3),
    "Focused ion beam": ("#1a7f37", WEEKDAYS, 30, 60, 12 * 60, 28, 2),
    "Transmission electron microscopy": ("#8250df", WEEKDAYS, 30, 60, 10 * 60, 28, 2),
    "Atom probe": ("#6639ba", ALL_WEEK, 60, 120, 48 * 60, 42, 2),
    "Surface analysis": ("#bf3989", ALL_WEEK, 30, 60, 24 * 60, 42, 2),
    "X-ray diffraction": ("#d4a72c", ALL_WEEK, 30, 30, 24 * 60, 28, 4),
    "Thermal analysis": ("#cf222e", ALL_WEEK, 60, 60, 72 * 60, 42, 3),
    "Scanning probe microscopy": ("#0a7c86", WEEKDAYS, 30, 30, 8 * 60, 28, 3),
    "Gas sorption": ("#9a6700", ALL_WEEK, 60, 120, 72 * 60, 42, 2),
    "Optical spectroscopy": ("#e16f24", WEEKDAYS, 30, 30, 8 * 60, 28, 3),
    "Mechanical testing": ("#57606a", WEEKDAYS, 30, 30, 8 * 60, 28, 2),
    "Electrochemistry": ("#0969da", ALL_WEEK, 60, 60, 72 * 60, 42, 3),
    "Thin-film deposition": ("#953800", WEEKDAYS, 30, 60, 10 * 60, 28, 2),
    "Optical microscopy & metrology": ("#4d2d00", WEEKDAYS, 30, 30, 8 * 60, 28, 3),
    "Sample preparation": ("#6e7781", OFFICE, 30, 30, 4 * 60, 14, 3),
}

FAC_APSL = "Advanced Photoelectron Spectroscopy Laboratory"
FAC_EM = "Electron Microscopy Facility"
FAC_CRYO = "Centre for Cryo Microscopy of Materials (I(CM)²)"
FAC_SAF = "Surface Analysis Facility"
FAC_XRD = "X-ray Diffraction Facility"
FAC_TA = "Thermal Analysis (departmental)"
FAC_CASC = "Centre for Advanced Structural Ceramics"
FAC_AFM = "AFM (departmental)"
FAC_ROYCE = "Royce at Imperial"
FAC_SHARED = "Shared characterisation lab"

M = "materials-website"
A = "added"

# id, name, make/model, category, facility, techniques, description, source,
# requires_permission, consumables, maintenance, extra overrides
CATALOGUE = [
    # --- Advanced Photoelectron Spectroscopy Laboratory -------------------
    ("xps", "High-throughput XPS", "", "Photoelectron spectroscopy", FAC_APSL,
     ["XPS", "depth profiling"],
     "Fully automated XPS for quantitative elemental composition and chemical "
     "state of the top few nanometres; unattended overnight queues.", M, True,
     [("X-ray source filament", 2000, 1800), ("Ion gun filament", 1000, 900)],
     [("Bake-out", 180), ("Energy-scale calibration (Au/Ag/Cu)", 30)], {}),
    ("nap-xps", "Near Ambient Pressure XPS", "", "Photoelectron spectroscopy", FAC_APSL,
     ["NAP-XPS", "XPS", "UPS", "LEED"],
     "In-situ XPS from UHV to tens of mbar at variable temperature: catalysis, "
     "gas–surface reactions. Also UPS (work function, valence band) and LEED.",
     M, True,
     [("X-ray source anode", 1500, 1300), ("UV lamp", 1000, 900)],
     [("Bake-out", 180), ("Energy-scale calibration (Au/Ag/Cu)", 30)],
     {"min_booking_minutes": 240, "slot_granularity_minutes": 60}),

    # --- Electron Microscopy Facility -------------------------------------
    ("sem-gemini-1525", "Zeiss LEO Gemini 1525 FEG-SEM", "Zeiss LEO Gemini 1525",
     "Scanning electron microscopy", FAC_EM, ["SEM", "EDS"],
     "Field-emission SEM for high-resolution imaging and EDS.", M, True,
     [("FEG emitter", 15000, 14000)], [("Service visit", 365)], {}),
    ("sem-sigma-300", "Zeiss Sigma 300 FEG-SEM", "Zeiss Sigma 300",
     "Scanning electron microscopy", FAC_EM, ["SEM", "EDS", "EBSD"],
     "Field-emission SEM for imaging, EDS and EBSD.", M, True,
     [("FEG emitter", 15000, 14000)], [("Service visit", 365)], {}),
    ("sem-quanta", "FEI Quanta FEG-ESEM", "FEI Quanta FEG",
     "Scanning electron microscopy", FAC_EM, ["SEM", "ESEM", "in-situ heating"],
     "Environmental FEG-SEM: hydrated or outgassing samples, low vacuum, in-situ stages.",
     M, True, [("FEG emitter", 15000, 14000)], [("Service visit", 365)], {}),
    ("sem-jeol-6010", "JEOL JSM-6010LA SEM", "JEOL JSM-6010LA",
     "Scanning electron microscopy", FAC_EM, ["SEM", "EDS"],
     "Tungsten-filament benchtop SEM with integrated EDS for routine imaging and teaching.",
     M, False, [("Tungsten filament", 100, 80)], [("Service visit", 365)],
     {"max_booking_minutes": 4 * 60}),
    ("fib-helios-5cx", "TFS Helios 5 CX DualBeam", "Thermo Fisher Helios 5 CX",
     "Focused ion beam", FAC_EM, ["FIB-SEM", "TEM lamella prep", "3D slice & view"],
     "Ga-ion DualBeam for site-specific TEM lamellae, cross-sections and serial sectioning.",
     M, True, [("Ga LMIS", 1500, 1300)], [("Service visit", 365)], {}),
    ("fib-auriga", "Zeiss Auriga CrossBeam FIB-SEM", "Zeiss Auriga",
     "Focused ion beam", FAC_EM, ["FIB-SEM", "EDS", "EBSD"],
     "CrossBeam FIB-SEM for cross-sections, lamellae and 3D tomography.", M, True,
     [("Ga LMIS", 1500, 1300)], [("Service visit", 365)], {}),
    ("tem-2100plus", "JEOL JEM-2100Plus TEM", "JEOL JEM-2100Plus",
     "Transmission electron microscopy", FAC_EM, ["TEM", "SAED", "EDS"],
     "200 kV LaB6 TEM for routine imaging and diffraction.", M, True,
     [("LaB6 filament", 1500, 1300)], [("Service visit", 365)], {}),
    ("tem-2100f", "JEOL JEM-2100F FEG-TEM", "JEOL JEM-2100F",
     "Transmission electron microscopy", FAC_EM, ["TEM", "STEM", "HRTEM", "EDS"],
     "200 kV field-emission TEM/STEM for high-resolution imaging and analysis.", M, True,
     [], [("Service visit", 365)], {}),
    ("tem-talos", "TFS Talos (S)TEM", "Thermo Fisher Talos",
     "Transmission electron microscopy", FAC_EM, ["TEM", "STEM", "EDS mapping"],
     "Analytical (S)TEM with fast EDS mapping.", M, True,
     [], [("Service visit", 365)], {}),

    # --- Centre for Cryo Microscopy of Materials --------------------------
    ("tem-spectra-300", "TFS Spectra 300 (S)TEM", "Thermo Fisher Spectra 300",
     "Transmission electron microscopy", FAC_CRYO,
     ["aberration-corrected STEM", "monochromated EELS", "cryo-TEM"],
     "Monochromated, aberration-corrected (S)TEM with EELS and cryo-holders.", M, True,
     [], [("Service visit", 182)], {"min_booking_minutes": 120}),
    ("pfib-hydra", "TFS Helios Hydra PFIB (cryo)", "Thermo Fisher Helios Hydra",
     "Focused ion beam", FAC_CRYO, ["plasma FIB", "cryo-FIB", "APT tip prep"],
     "Multi-ion-species plasma FIB with cryo-stage for large-volume milling and "
     "cryo lift-out.", M, True, [], [("Service visit", 365)], {}),
    ("apt-leap-5000", "Cameca LEAP 5000 XR atom probe", "Cameca LEAP 5000 XR",
     "Atom probe", FAC_CRYO, ["atom probe tomography"],
     "Local-electrode atom probe for 3D near-atomic compositional mapping.", M, True,
     [], [("Service visit", 365)], {}),

    # --- Surface Analysis Facility ----------------------------------------
    ("tofsims-leis", "IONTOF TOF.SIMS5–Qtac100 LEIS", "IONTOF TOF.SIMS 5 + Qtac100",
     "Surface analysis", FAC_SAF, ["ToF-SIMS", "LEIS", "isotope tracer profiling"],
     "Combined ToF-SIMS and low-energy ion scattering: outermost-monolayer composition "
     "and isotopic depth profiles.", M, True,
     [("Bi LMIG", 2000, 1800)], [("Bake-out", 365)], {}),
    ("fib-sims", "FEI FIB-SIMS", "FEI FIB-SIMS",
     "Surface analysis", FAC_SAF, ["FIB", "SIMS imaging"],
     "Focused ion beam with secondary-ion mass spectrometry for high-resolution "
     "chemical imaging.", M, True, [("Ga LMIS", 1500, 1300)], [], {}),
    ("zygo-newview-200", "Zygo NewView 200 interferometer", "Zygo NewView 200",
     "Optical microscopy & metrology", FAC_SAF, ["coherence scanning interferometry"],
     "Non-contact surface topography; SIMS crater depths.", M, False, [], [],
     {"max_booking_minutes": 4 * 60}),

    # --- X-ray Diffraction Facility ---------------------------------------
    ("xrd-empyrean-1", "PANalytical Empyrean #1", "Malvern Panalytical Empyrean",
     "X-ray diffraction", FAC_XRD, ["powder XRD", "thin films", "HT-XRD"],
     "Multi-purpose diffractometer, PIXcel 2D detector, Anton Paar heating chamber "
     "to 1000 °C.", M, True, [("Cu X-ray tube", 20000, 18000)], [("Alignment check", 90)], {}),
    ("xrd-empyrean-2", "PANalytical Empyrean #2", "Malvern Panalytical Empyrean",
     "X-ray diffraction", FAC_XRD, ["SAXS/WAXS", "high-resolution thin film", "texture"],
     "Empyrean with triple-axis analyser and ScatterX78 SAXS/WAXS attachment.", M, True,
     [("Cu X-ray tube", 20000, 18000)], [("Alignment check", 90)], {}),
    ("xrd-mrd-1", "PANalytical X'Pert MRD #1", "PANalytical X'Pert MRD",
     "X-ray diffraction", FAC_XRD, ["HRXRD", "reciprocal space maps", "XRR"],
     "Materials research diffractometer for epitaxial films and reflectivity.", M, True,
     [("Cu X-ray tube", 20000, 18000)], [("Alignment check", 90)], {}),
    ("xrd-mrd-2", "PANalytical X'Pert MRD #2", "PANalytical X'Pert MRD",
     "X-ray diffraction", FAC_XRD, ["HRXRD", "texture", "residual stress"],
     "Materials research diffractometer with Eulerian cradle.", M, True,
     [("Cu X-ray tube", 20000, 18000)], [("Alignment check", 90)], {}),
    ("xrd-mpd", "PANalytical X'Pert MPD", "PANalytical X'Pert MPD",
     "X-ray diffraction", FAC_XRD, ["powder XRD"],
     "Powder diffractometer for phase identification.", M, True,
     [("Cu X-ray tube", 20000, 18000)], [("Alignment check", 90)], {}),
    ("xrd-d2-1", "Bruker D2 Phaser #1", "Bruker D2 Phaser",
     "X-ray diffraction", FAC_XRD, ["powder XRD"],
     "Desktop diffractometer for rapid routine phase ID.", M, False,
     [("Cu X-ray tube", 20000, 18000)], [], {"max_booking_minutes": 4 * 60}),
    ("xrd-d2-2", "Bruker D2 Phaser #2", "Bruker D2 Phaser",
     "X-ray diffraction", FAC_XRD, ["powder XRD"],
     "Desktop diffractometer for rapid routine phase ID.", M, False,
     [("Cu X-ray tube", 20000, 18000)], [], {"max_booking_minutes": 4 * 60}),

    # --- Thermal analysis --------------------------------------------------
    ("sta-449c", "Netzsch STA 449 C Jupiter", "Netzsch STA 449 C Jupiter",
     "Thermal analysis", FAC_TA, ["TGA", "DSC"],
     "Simultaneous TGA/DSC, 25–1500 °C.", M, True,
     [("Sample carrier thermocouple", 3000, 2700)], [("Temperature calibration", 180)], {}),
    ("sta-449f5", "Netzsch STA 449 F5 Jupiter", "Netzsch STA 449 F5 Jupiter",
     "Thermal analysis", FAC_TA, ["TGA", "DSC"],
     "Simultaneous TGA/DSC, 25–1500 °C.", M, True,
     [("Sample carrier thermocouple", 3000, 2700)], [("Temperature calibration", 180)], {}),
    ("dil-402e", "Netzsch DIL 402 E dilatometer", "Netzsch DIL 402 E",
     "Thermal analysis", FAC_TA, ["dilatometry", "CTE", "sintering"],
     "Push-rod dilatometer to 1500 °C.", M, True, [], [("Calibration run", 180)], {}),
    ("casc-dta-tga", "CASC DTA/TGA (2000 °C)", "",
     "Thermal analysis", FAC_CASC, ["DTA", "TGA"],
     "Ultra-high-temperature DTA/TGA up to 2000 °C.", M, True, [], [], {}),
    ("casc-laserflash", "CASC laser flash (2000 °C)", "",
     "Thermal analysis", FAC_CASC, ["thermal diffusivity", "thermal conductivity"],
     "Laser flash thermal diffusivity/conductivity to 2000 °C.", M, True, [], [], {}),
    ("casc-dilatometer", "CASC dilatometer (2400 °C)", "",
     "Thermal analysis", FAC_CASC, ["dilatometry"],
     "Ultra-high-temperature dilatometer to 2400 °C.", M, True, [], [], {}),

    # --- AFM ---------------------------------------------------------------
    ("afm-innova", "Bruker Innova AFM", "Bruker Innova",
     "Scanning probe microscopy", FAC_AFM, ["tapping", "contact", "C-AFM"],
     "Routine AFM: tapping, contact and conductive modes.", M, False, [], [], {}),
    ("afm-mfp3d", "Asylum MFP-3D Classic AFM", "Asylum Research MFP-3D Classic",
     "Scanning probe microscopy", FAC_AFM, ["KPFM", "MFM", "PFM", "force mapping", "AM-FM"],
     "Advanced AFM with liquid, electrochemical, heater and variable-field cells.", M, True,
     [], [], {}),

    # --- Royce -------------------------------------------------------------
    ("royce-confocal", "Royce scanning confocal microscope", "",
     "Optical microscopy & metrology", FAC_ROYCE, ["confocal microscopy", "3D topography"],
     "Laser scanning confocal microscope (Royce charge-out).", M, True, [], [], {}),
    ("royce-ec-ms", "Royce electrochemical mass spectrometry", "",
     "Electrochemistry", FAC_ROYCE, ["DEMS", "online EC-MS"],
     "Differential electrochemical mass spectrometry for evolved-gas analysis.", M, True,
     [], [], {}),
    ("royce-sputter", "Royce thin-film sputter system", "",
     "Thin-film deposition", FAC_ROYCE, ["magnetron sputtering"],
     "Magnetron sputter deposition of metals, oxides and nitrides.", M, True,
     [("Target hours", 500, 450)], [], {}),

    # --- Added: common kit beyond the website listing ----------------------
    ("bet", "Gas sorption analyser (BET)", "",
     "Gas sorption", FAC_APSL, ["BET surface area", "pore size (BJH)"],
     "N2/Ar/CO2 physisorption: surface area and porosity.", A, True, [], [], {}),
    ("raman", "Confocal Raman microscope", "",
     "Optical spectroscopy", FAC_SHARED, ["Raman", "PL", "mapping"],
     "Raman and photoluminescence with 532/633/785 nm lasers and mapping stage.", A, True,
     [], [("Si calibration", 7)], {}),
    ("ftir", "FTIR spectrometer (ATR/DRIFTS)", "",
     "Optical spectroscopy", FAC_SHARED, ["FTIR", "ATR", "DRIFTS"],
     "Mid-IR spectroscopy with ATR and in-situ DRIFTS cell.", A, False, [], [],
     {"max_booking_minutes": 4 * 60}),
    ("uv-vis", "UV-Vis-NIR spectrophotometer", "",
     "Optical spectroscopy", FAC_SHARED, ["UV-Vis-NIR", "diffuse reflectance"],
     "Transmission and integrating-sphere reflectance, 200–2500 nm.", A, False, [], [],
     {"max_booking_minutes": 4 * 60}),
    ("ellipsometer", "Spectroscopic ellipsometer", "",
     "Optical spectroscopy", FAC_SHARED, ["ellipsometry", "film thickness"],
     "Variable-angle spectroscopic ellipsometry for thin-film thickness and optical constants.",
     A, True, [], [], {}),
    ("nanoindenter", "Nanoindenter", "",
     "Mechanical testing", FAC_SHARED, ["nanoindentation", "hardness", "modulus"],
     "Instrumented indentation with Berkovich and spherical tips.", A, True, [], [],
     {"max_booking_minutes": 24 * 60, "bookable_hours": ALL_WEEK}),
    ("uts", "Universal testing machine (100 kN)", "",
     "Mechanical testing", FAC_SHARED, ["tensile", "compression", "flexure"],
     "Screw-driven load frame with extensometer and furnace.", A, True, [], [], {}),
    ("potentiostat", "Potentiostat / impedance analyser", "",
     "Electrochemistry", FAC_SHARED, ["EIS", "CV", "chronoamperometry"],
     "Multichannel potentiostat with frequency-response analyser.", A, False, [], [], {}),
    ("stylus-profiler", "Stylus profilometer", "",
     "Optical microscopy & metrology", FAC_SHARED, ["step height", "roughness"],
     "Contact profilometry for film thickness and roughness.", A, False, [], [],
     {"max_booking_minutes": 2 * 60}),
    ("optical-microscope", "Optical microscope (polarised)", "",
     "Optical microscopy & metrology", FAC_SHARED, ["bright field", "dark field", "polarised"],
     "Reflected/transmitted light microscope with camera.", A, False, [], [],
     {"max_booking_minutes": 4 * 60}),
    ("glovebox", "Argon glovebox", "",
     "Sample preparation", FAC_SHARED, ["air-sensitive handling"],
     "Ar glovebox (<1 ppm O2/H2O) with transfer vessel for XPS/SEM.", A, True,
     [("Purifier regeneration", 2000, 1800)], [("Regeneration", 180)],
     {"bookable_hours": ALL_WEEK, "max_booking_minutes": 8 * 60}),
    ("sputter-coater", "Sputter coater (Au/C)", "",
     "Sample preparation", FAC_EM, ["Au coating", "C coating"],
     "Conductive coating for SEM samples.", A, False, [("Au target", 200, 180)], [],
     {"max_booking_minutes": 60}),
    ("pips", "Precision ion polishing system", "",
     "Sample preparation", FAC_EM, ["Ar ion milling", "TEM sample prep"],
     "Low-angle Ar ion milling for electron-transparent TEM foils.", A, True, [], [], {}),
]


def instrument_dict(row) -> dict:
    (iid, name, make, cat, fac, techniques, desc, source, perm,
     consumables, maintenance, extra) = row
    colour, hours, gran, mn, mx, adv, conc = CATEGORIES[cat]
    d = {
        "id": iid,
        "name": name,
        "make_model": make,
        "category": cat,
        "facility": fac,
        "techniques": techniques,
        "description": desc,
        "colour": colour,
        "location": "",
        "source": source,
        "bookable_hours": hours,
        "slot_granularity_minutes": gran,
        "min_booking_minutes": mn,
        "max_booking_minutes": mx,
        "max_advance_days": adv,
        "max_concurrent_per_user": conc,
        "requires_permission": perm,
        "consumables": [
            {"name": n, "limit_hours": lim, "warn_at": warn} for n, lim, warn in consumables
        ],
        "maintenance": [
            {"task": t, "interval_days": days} for t, days in maintenance
        ],
    }
    d.update(extra)
    return d


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.yaml"):
        old.unlink()
    seen = set()
    for row in CATALOGUE:
        d = instrument_dict(row)
        if d["id"] in seen:
            raise SystemExit(f"duplicate instrument id {d['id']}")
        seen.add(d["id"])
        (OUT / f"{d['id']}.yaml").write_text(dump_yaml(d), encoding="utf-8")
    print(f"wrote {len(seen)} instruments to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
