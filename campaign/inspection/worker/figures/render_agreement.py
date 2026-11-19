#!/usr/bin/env python3
"""Render campaign agreement figures from compact, observed plot data."""

from __future__ import annotations

import csv
import gzip
from hashlib import file_digest
import json
from pathlib import Path
from typing import Annotated

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator, ScalarFormatter
import numpy as np
import typer


PALETTE = {"A": "#21262e", "B": "#ad4e19", "C": "#116b8d"}
STYLE = {"A": "-", "B": (0, (6, 3)), "C": (3, (2, 4))}
NAMES = {"A": "A · Release AnalysisBase", "B": "B · Replacement in AnalysisBase", "C": "C · Columnar"}
RESULT_COLORS = ("#e6e9eb", "#f3cf89", "#328fa8", "#a63f43")
RESULT_LABELS = ("Equal values", "Equal values, different stored bits", "Within tolerance", "Outside acceptance")
DISPLAY_STATUS = {"-1": "Skipped", "0": "Error", "1": "OOV", "2": "OK"}
REPRESENTATIVE = ("MUON_SCALE_CB__1up", "MUON_SAGITTA_GLOBAL__1down")

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 10,
    "axes.spines.top": False, "axes.spines.right": False, "axes.labelcolor": "#242a33",
    "axes.edgecolor": "#7a8690", "xtick.color": "#394651", "ytick.color": "#394651",
    "axes.titlelocation": "left", "figure.facecolor": "white", "pdf.fonttype": 42,
    "svg.fonttype": "none", "savefig.facecolor": "white",
})


def record(bundle, name, state):
    quantity = bundle["quantities"][name]
    return quantity["records"][quantity["effective_state"][state]]


def mismatch_count(summary):
    return summary["finite_pairs"] - summary["equal_finite"] + summary["nonfinite_class_mismatches"]


def read_bundles(paths):
    bundles = {}
    for path in paths:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            data = json.load(stream)
        if data["schema_version"] != 1 or data["candidate"] not in {"B", "C"}:
            raise ValueError(f"Unsupported plot data at {path}")
        label = data["candidate"]
        if label in bundles:
            raise ValueError(f"Provide at most one plot bundle per candidate. Got another {label}")
        if bundles:
            first = next(iter(bundles.values()))
            fields = ("source_uuid", "entry_range", "muons", "states", "weight_only",
                      "object_identity_sha256", "tolerances")
            if any(first[key] != data[key] for key in fields):
                raise ValueError("Paired plot data use different input ranges, states or tolerances")
            for name in ("reference", "reference_histogram", "reference_systematics", "reference_audit"):
                if first["provenance"][name]["sha256"] != data["provenance"][name]["sha256"]:
                    raise ValueError(f"Paired plot data use different Path A artifacts for {name}")
        bundles[label] = data
    if not bundles:
        raise ValueError("Supply at least one plot bundle")
    return dict(sorted(bundles.items()))


def line_handles(labels):
    return [Line2D([0], [0], color=PALETTE[name], linestyle=STYLE[name], lw=2,
                   label=NAMES[name]) for name in ["A", *labels]]


def plot_meta(bundles):
    first = next(iter(bundles.values()))
    accepted = " · ".join(f"{name}/A {'accepted' if bundle['accepted'] else 'outside acceptance'}"
                          for name, bundle in bundles.items())
    events = first["entry_range"][1] - first["entry_range"][0]
    return f"MC23a · DSID 601229 · {events:,} events · {first['muons']:,} source muons · {accepted}"


def state_label(state):
    if state == "NOSYS":
        return "Nominal"
    return state.removeprefix("MUON_").replace("EFF_", "").replace("__1up", " ↑").replace("__1down", " ↓")


def mark_y(ax, *, fractional=False):
    ax.axhline(0, lw=0.8, color="#8b939b", zorder=0)
    ax.yaxis.set_major_locator(MaxNLocator(4, integer=not fractional))
    if fractional:
        formatter = ScalarFormatter(useMathText=True)
        formatter.set_powerlimits((-2, 3))
        formatter.set_useOffset(False)
        ax.yaxis.set_major_formatter(formatter)


def keep_residuals(ax, absolute, *, fractional=False):
    if absolute > 0:
        ax.set_ylim(-1.16 * absolute, 1.16 * absolute)
    else:
        ax.set_ylim(-1, 1)
        ax.set_yticks([0])
    if fractional and absolute > 0:
        ax.margins(x=0)


def overlay(ax, edges, line, label, *, width=1.8):
    ax.stairs(line, edges, color=PALETTE[label], linestyle=STYLE[label], lw=width)


def format_nonfinite(summary, key):
    values = summary[key]
    return str(sum(values))


def continuous_panel(axes, bundles, variable, state, variant, compact=False):
    top, bin_residual, paired = axes
    primary = next(iter(bundles.values()))
    quantity = primary["quantities"][variable]
    edges = np.asarray(quantity["axis"][variant])
    centers = (edges[1:] + edges[:-1]) / 2
    first = record(primary, variable, state)
    a = np.asarray(first["plots"][variant]["A"])
    title = quantity["title"]
    unit = quantity["unit"]
    overlay(top, edges, a[1:-1], "A")
    max_bin_diff, max_pair_diff = 0.0, 0.0
    footer = [f"A flow {a[0]}/{a[-1]}, nonfinite {format_nonfinite(first['summary'], 'nonfinite_A')}"]
    paired_notes = []
    num_max = max(a[1:-1], default=0)
    for name, bundle in bundles.items():
        observed = record(bundle, variable, state)
        display = observed["plots"][variant]
        candidate = np.asarray(display["candidate"])
        overlay(top, edges, candidate[1:-1], name)
        num_max = max(num_max, max(candidate[1:-1], default=0))
        delta = candidate[1:-1] - a[1:-1]
        overlay(bin_residual, edges, delta, name, width=1.25)
        max_bin_diff = max(max_bin_diff, float(np.max(np.abs(delta), initial=0.0)))
        minima = np.asarray(display["min_signed"][1:-1], dtype=float)
        maxima = np.asarray(display["max_signed"][1:-1], dtype=float)
        present = np.isfinite(minima) & np.isfinite(maxima)
        observed_abs = max(float(np.max(np.abs(minima[present]), initial=0.0)),
                           float(np.max(np.abs(maxima[present]), initial=0.0)))
        max_pair_diff = max(max_pair_diff, observed_abs)
        if observed_abs:
            nonzero = present & ((minima != 0) | (maxima != 0))
            paired.vlines(centers[nonzero], minima[nonzero], maxima[nonzero],
                          color=PALETTE[name], linewidth=1.45)
            paired.scatter(centers[nonzero], maxima[nonzero], color=PALETTE[name], marker=".",
                           s=16, linewidths=0)
            paired.scatter(centers[nonzero], minima[nonzero], color=PALETTE[name], marker=".",
                           s=16, linewidths=0)
        summary = observed["summary"]
        if summary["max_abs"] == 0 and summary["nonfinite_class_mismatches"] == 0:
            paired_notes.append(f"{name} · {summary['finite_pairs']:,} finite pairs equal")
        elif not observed_abs:
            paired_notes.append(f"{name} · residuals outside displayed bins or nonfinite")
        else:
            unit_text = f" {unit}" if unit else ""
            paired_notes.append(f"{name} · max |Δ| {summary['max_abs']:.3g}{unit_text} over all pairs")
        footer.append(f"{name} flow {candidate[0]}/{candidate[-1]}, nonfinite "
                      f"{format_nonfinite(summary, 'nonfinite_candidate')}")
    if num_max > 80:
        top.set_yscale("log")
        top.set_ylim(bottom=0.8)
    else:
        top.set_ylim(bottom=0)
    top.set_title(title, weight="semibold", fontsize=11)
    top.set_ylabel("Muon entries")
    mark_y(bin_residual)
    keep_residuals(bin_residual, max_bin_diff)
    bin_residual.set_ylabel("Δ entries")
    mark_y(paired, fractional=True)
    keep_residuals(paired, max_pair_diff, fractional=True)
    paired.set_ylabel(f"Paired Δ [{unit}]" if unit else "Paired Δ")
    paired.set_xlabel(f"{title} [{unit}]" if unit else title)
    paired.set_xlim(edges[0], edges[-1])
    if not max_bin_diff:
        bin_residual.text(0.98, 0.82, "Equal in-range counts", transform=bin_residual.transAxes,
                          ha="right", va="top", fontsize=8)
    paired.text(0.98, 0.93, "\n".join(paired_notes), transform=paired.transAxes,
                ha="right", va="top", fontsize=7.8 if compact else 9,
                bbox={"facecolor": "white", "edgecolor": "none", "alpha": .8, "pad": 1})
    pop = quantity["population"]
    if "excluded_A" in first:
        pop += f" · {first['excluded_A']:,} rows excluded"
    if "A_oov" in first:
        oov = [f"A {first['A_oov']}"] + [f"{label} {record(data, variable, state)['candidate_oov']}"
                                        for label, data in bundles.items()]
        pop += " · OOV " + ", ".join(oov)
    return pop, "   ·   ".join(footer)


def continuous_figure(bundles, variable, state, variant="full"):
    fig, axes = plt.subplots(3, 1, figsize=(9.6, 6.6), sharex=True,
                             gridspec_kw={"height_ratios": [3.2, 1.05, 1.35]})
    fig.subplots_adjust(left=.13, right=.97, top=.84, bottom=.19, hspace=.13)
    title, footer = continuous_panel(axes, bundles, variable, state, variant)
    fig.text(.13, .956, f"Matched muon objects · {state_label(state)}", fontsize=15, weight="bold")
    fig.legend(handles=line_handles(bundles), loc="upper right", bbox_to_anchor=(.98, .925),
               fontsize=9, frameon=False, ncol=len(bundles)+1)
    fig.text(.13, .088, title, fontsize=9)
    fig.text(.13, .057, footer, fontsize=8)
    fig.text(.13, .025, plot_meta(bundles), fontsize=8, color="#526470")
    return fig


def representative_figure(bundles):
    fig, axes = plt.subplots(3, 2, figsize=(12.2, 6.4), sharex="col",
                             gridspec_kw={"height_ratios": [3.2, 1.05, 1.35]})
    fig.subplots_adjust(left=.09, right=.975, top=.83, bottom=.17, hspace=.16, wspace=.32)
    for column, (quantity, variant) in enumerate((("combined_pt", "full"), ("combined_sf", "core"))):
        population, flows = continuous_panel(axes[:, column], bundles, quantity, "NOSYS", variant, compact=True)
        x = axes[-1, column].get_position().x0
        fig.text(x, .078, population, fontsize=8)
        fig.text(x, .050, flows.replace("   ·   ", "  ·  "), fontsize=7)
    fig.text(.09, .948, "Nominal calibrated momentum and object weight", fontsize=17, weight="bold")
    fig.legend(handles=line_handles(bundles), loc="upper right", bbox_to_anchor=(.98, .918),
               fontsize=9, frameon=False, ncol=len(bundles)+1)
    fig.text(.09, .017, plot_meta(bundles), fontsize=8, color="#526470")
    return fig


def category_label(category, kind):
    if kind == "status":
        return DISPLAY_STATUS.get(category, category)
    if kind == "bool":
        return {"0": "False", "1": "True"}.get(category, category)
    if kind == "bits":
        return {"0": "Unset/failed", "4294967294": "Failed", "4294967295": "Accepted"}.get(category, category)
    if kind == "track" and category.isdigit():
        bits = int(category)
        return f"0x{bits & 7:X}" if bits >= 0xfffffff8 else f"0x{bits:08X}"
    return category


def sort_category(key):
    try:
        return (0, float(key))
    except ValueError:
        return (1, key)


def discrete_figure(bundles, names, state):
    first = next(iter(bundles.values()))
    rows = (len(names) + 1) // 2
    fig, axes = plt.subplots(rows, 2, figsize=(10.8, max(5.7, rows * 2.65)), squeeze=False)
    fig.subplots_adjust(left=.10, right=.985, top=.79, bottom=.15, wspace=.29, hspace=.88)
    for ax, name in zip(axes.flat, names):
        quantity = first["quantities"][name]
        ref = record(first, name, state)
        categories = {"A": ref["categories"]["A"]}
        counts = []
        for label, data in bundles.items():
            observed = record(data, name, state)
            categories[label] = observed["categories"]["candidate"]
            counts.append(f"{label}/A {mismatch_count(observed['summary']):,}")
        keys = sorted(set().union(*(counts.keys() for counts in categories.values())), key=sort_category)
        if quantity["kind"] == "status":
            keys = sorted(set(keys) | set(DISPLAY_STATUS), key=sort_category)
        x = np.arange(len(keys))
        width = 0.75 / len(categories)
        for offset, (label, bins) in enumerate(categories.items()):
            values = [bins.get(key, 0) for key in keys]
            ax.bar(x + (offset - (len(categories) - 1) / 2) * width, values, width,
                   color=PALETTE[label])
        ax.set_xticks(x, [category_label(key, quantity["kind"]) for key in keys], fontsize=8)
        ax.set_title(quantity["title"], weight="semibold", fontsize=10)
        ax.set_ylabel("Muons")
        ax.set_yscale("symlog", linthresh=1)
        ax.set_ylim(bottom=0)
        ax.text(.98, .95, "Paired mismatches " + " · ".join(counts), transform=ax.transAxes,
                ha="right", va="top", fontsize=7.5,
                bbox={"facecolor": "white", "edgecolor": "none", "alpha": .85, "pad": 1})
    for ax in list(axes.flat)[len(names):]:
        ax.axis("off")
    fig.text(.10, .95, f"Selection, charge and return codes · {state_label(state)}", fontsize=15, weight="bold")
    fig.legend(handles=[Patch(color=PALETTE[label], label=NAMES[label]) for label in ("A", *bundles)],
               loc="upper right", bbox_to_anchor=(.99, .917), frameon=False, ncol=len(bundles)+1, fontsize=9)
    fig.text(.10, .052, "Symmetric log count scale. Track mask labels show the three active bits.", fontsize=8)
    fig.text(.10, .025, plot_meta(bundles), fontsize=8, color="#526470")
    return fig


def threshold_figure(bundles):
    first = next(iter(bundles.values()))
    fig, axes = plt.subplots(2, 2, figsize=(11, 5.8), sharex="col",
                             gridspec_kw={"height_ratios": [2.7, 1.2]})
    fig.subplots_adjust(left=.095, right=.98, top=.82, bottom=.21, hspace=.15, wspace=.30)
    for col, key in enumerate(("pt", "eta")):
        spec = first["thresholds"][key]
        original = spec["records"]["NOSYS"]
        top, residual = axes[:, col]
        edges = np.asarray(spec["edges"])
        n_changed, extent = [], 0
        for kind, alpha in (("pass", 1.0), ("fail", 0.45)):
            series = np.asarray(original[f"A_{kind}"])
            top.stairs(series[1:-1], edges, color=PALETTE["A"], alpha=alpha, lw=1.85)
            for name, bundle in bundles.items():
                obs = bundle["thresholds"][key]["records"]["NOSYS"]
                if kind == "pass":
                    n_changed.append(f"{name}/A changed gates {obs['paired_changed_gates']:,}")
                result = np.asarray(obs[f"candidate_{kind}"])
                top.stairs(result[1:-1], edges, color=PALETTE[name], alpha=alpha,
                           linestyle=STYLE[name], lw=1.6)
                delta = result[1:-1] - series[1:-1]
                residual.stairs(delta, edges, color=PALETTE[name], alpha=alpha,
                                linestyle=STYLE[name], lw=1.45)
                extent = max(extent, float(np.max(np.abs(delta), initial=0.0)))
        for ax in (top, residual):
            ax.axvline(spec["cut"], color="#414952", lw=1, ls=":")
            ax.set_xlim(edges[0], edges[-1])
        top.set_ylabel("Muon entries")
        top.set_title("Calibrated pₜ ≥ 3 GeV" if key == "pt" else "Raw |η| ≤ 2.7", fontsize=11, weight="semibold")
        top.yaxis.set_major_locator(MaxNLocator(5, integer=True))
        mark_y(residual)
        keep_residuals(residual, extent)
        residual.set_ylabel("Δ entries")
        residual.set_xlabel(spec["axis_label"])
        residual.text(.97, .92, "\n".join(n_changed), transform=residual.transAxes,
                      ha="right", va="top", fontsize=8)
    fig.text(.095, .944, "Muon decisions near the kinematic cuts", fontsize=16, weight="bold")
    fig.legend(handles=line_handles(bundles), loc="upper right", bbox_to_anchor=(.99, .914),
               frameon=False, fontsize=9, ncol=len(bundles)+1)
    fig.text(.095, .096, "Dark traces pass the cut. Lighter traces fail. Candidate residuals are candidate minus A.", fontsize=9)
    fig.text(.095, .061, "The pₜ panel uses the same muons passing the release raw eta gate in every path.", fontsize=8)
    fig.text(.095, .028, plot_meta(bundles), fontsize=8, color="#526470")
    return fig


def endpoint_figure(bundles, state):
    first = next(iter(bundles.values()))
    edges = np.asarray(first["endpoint"]["edges"])
    a = first["endpoint"]["records"][state]["A"]
    fig, axes = plt.subplots(3, 1, figsize=(9.9, 6.2), sharex=True,
                             gridspec_kw={"height_ratios": [3.0, 1.22, 1.22]})
    fig.subplots_adjust(left=.13, right=.97, top=.83, bottom=.21, hspace=.14)
    top, sumw, sumw2 = axes
    overlay(top, edges, a["sumw"][1:-1], "A")
    ranges = {"sumw": 0.0, "sumw2": 0.0}
    flows = [f"A {a['count'][0]}/{a['count'][-1]}"]
    flow_weights = [f"A {a['sumw'][0]:.6g}/{a['sumw'][-1]:.6g}"]
    all_nonnegative = np.min(a["sumw"]) >= 0
    for name, bundle in bundles.items():
        obs = bundle["endpoint"]["records"][state]["candidate"]
        overlay(top, edges, obs["sumw"][1:-1], name)
        all_nonnegative &= np.min(obs["sumw"]) >= 0
        flows.append(f"{name} {obs['count'][0]}/{obs['count'][-1]}")
        flow_weights.append(f"{name} {obs['sumw'][0]:.6g}/{obs['sumw'][-1]:.6g}")
        for key, ax in (("sumw", sumw), ("sumw2", sumw2)):
            residual = np.asarray(obs[key]) - np.asarray(a[key])
            overlay(ax, edges, residual[1:-1], name, width=1.25)
            ranges[key] = max(ranges[key], float(np.max(np.abs(residual[1:-1]), initial=0.0)))
    if all_nonnegative and np.any(np.asarray(a["sumw"][1:-1]) > 0):
        top.set_yscale("log")
    top.set_ylabel("Sum of weights")
    for key, ax, label in (("sumw", sumw, "Δ sumw"), ("sumw2", sumw2, "Δ sumw²")):
        mark_y(ax, fractional=True)
        keep_residuals(ax, ranges[key], fractional=True)
        ax.set_ylabel(label)
        if not ranges[key]:
            ax.text(.98, .88, "Equal in-range sums", transform=ax.transAxes, ha="right", va="top", fontsize=8)
    sumw2.set_xlabel("Selected muon pₜ [GeV]")
    sumw2.set_xlim(edges[0], edges[-1])
    fig.text(.13, .95, f"Weighted muon pₜ endpoint · {state_label(state)}", fontsize=15, weight="bold")
    fig.legend(handles=line_handles(bundles), loc="upper right", bbox_to_anchor=(.99, .917),
               frameon=False, ncol=len(bundles)+1, fontsize=9)
    fig.text(.13, .101, "Underflow / overflow entries   ·   " + "   ·   ".join(flows), fontsize=8.5)
    fig.text(.13, .071, "Underflow / overflow sumw   ·   " + "   ·   ".join(flow_weights), fontsize=8.5)
    fig.text(.13, .032, plot_meta(bundles), fontsize=8, color="#526470")
    return fig


def summary_class(summary):
    if summary["failed"]:
        return 3
    if mismatch_count(summary):
        return 2
    exact = summary["equal_stored_bits"]
    return int(exact is not None and exact != summary["pairs"])


def state_map(bundle, selected, start=0):
    quantities = bundle["quantities"]
    outcomes = np.zeros((len(quantities), len(selected)), dtype=int)
    for yi, name in enumerate(quantities):
        for xi, state in enumerate(selected):
            outcomes[yi, xi] = summary_class(record(bundle, name, state)["summary"])
    fig, ax = plt.subplots(figsize=(13.4, 9.6))
    fig.subplots_adjust(left=.25, right=.985, top=.87, bottom=.23)
    ax.imshow(outcomes, cmap=ListedColormap(RESULT_COLORS), norm=BoundaryNorm(np.arange(-.5, 4.5), 4),
              aspect="auto", interpolation="nearest")
    ax.set_yticks(np.arange(len(quantities)), [item["title"] for item in quantities.values()], fontsize=8)
    ax.set_xticks(np.arange(len(selected)), [state_label(state) for state in selected], rotation=55,
                  ha="right", rotation_mode="anchor", fontsize=7.5)
    for yi, (name, variable) in enumerate(quantities.items()):
        for xi, state in enumerate(selected):
            stats = record(bundle, name, state)["summary"]
            alias = variable["effective_state"][state] != state and state != "NOSYS"
            if outcomes[yi, xi] == 3:
                text = str(stats["failed"])
            elif outcomes[yi, xi] == 2:
                fraction = stats["max_tolerance_fraction"]
                text = f"{fraction:.2g}" if fraction is not None else "Δ"
            else:
                text = "·" if alias else ""
            if text:
                ax.text(xi, yi, text, ha="center", va="center", fontsize=7,
                        color="white" if outcomes[yi, xi] >= 2 else "#64747c")
    ax.set_xticks(np.arange(len(selected)+1)-.5, minor=True)
    ax.set_yticks(np.arange(len(quantities)+1)-.5, minor=True)
    ax.grid(which="minor", color="white", linewidth=.55)
    ax.tick_params(which="both", length=0)
    fig.text(.045, .954, f"{bundle['candidate']} / A agreement across systematic states",
             fontsize=16, weight="bold")
    fig.text(.045, .918, f"States {start+1}–{start+len(selected)} of {len(bundle['states'])} in release order",
             fontsize=9)
    fig.legend(handles=[Patch(color=color, label=label) for color, label in zip(RESULT_COLORS, RESULT_LABELS, strict=True)],
               loc="upper right", bbox_to_anchor=(.99, .928), fontsize=8, frameon=False, ncol=2)
    fig.text(.045, .052, "· Nominal alias.  Blue labels show maximum fraction of the allowed object residual.", fontsize=8.5)
    fig.text(.045, .028, "Red labels count failed pairs. The accompanying CSV retains signed extrema and exact counts.", fontsize=8.5)
    return fig


def endpoint_systematics(bundles):
    first = next(iter(bundles.values()))
    states = first["states"]
    fig, axes = plt.subplots(3, 1, figsize=(11, 7), sharex=True)
    fig.subplots_adjust(left=.12, right=.97, top=.82, bottom=.16, hspace=.29)
    peak = {"count": 0., "sumw": 0., "sumw2": 0.}
    for label, bundle in bundles.items():
        for ax, key in zip(axes, ("count", "sumw", "sumw2"), strict=True):
            maxima = []
            for state in states:
                payload = bundle["endpoint"]["records"][state]
                difference = np.asarray(payload["candidate"][key]) - np.asarray(payload["A"][key])
                maxima.append(np.max(np.abs(difference), initial=0))
            peak[key] = max(peak[key], max(maxima))
            ax.plot(np.arange(1, len(states)+1), maxima, color=PALETTE[label],
                    linestyle=STYLE[label], label=f"{label} − A", lw=1.65)
            ax.set_ylabel(f"max |Δ {key}|")
            mark_y(ax, fractional=key != "count")
    for ax, key in zip(axes, peak, strict=True):
        keep_residuals(ax, peak[key], fractional=key != "count")
    axes[-1].set_xlabel("Systematic state index in release order")
    axes[-1].set_xlim(.5, len(states)+.5)
    fig.text(.12, .951, "Selected pₜ histogram agreement across all states", fontsize=16, weight="bold")
    fig.legend(handles=[Line2D([0], [0], color=PALETTE[name], ls=STYLE[name], label=f"{name} − A") for name in bundles],
               loc="upper right", bbox_to_anchor=(.98, .905), fontsize=9, frameon=False)
    fig.text(.12, .054, "Each point is the largest absolute difference across 100 bins and both flow bins.", fontsize=9)
    fig.text(.12, .025, plot_meta(bundles), fontsize=8.5, color="#526470")
    return fig


def detailed_states(bundles, names, detail_all):
    first = next(iter(bundles.values()))
    states = first["states"]
    altered = []
    for state in states[1:]:
        if detail_all or state in REPRESENTATIVE or any(
            record(data, key, state)["summary"]["failed"] for data in bundles.values() for key in names
        ):
            altered.append(state)
    return ["NOSYS", *altered]


def write_summary(output, bundles):
    header = ("candidate", "state", "variable", "effective_state", "unit", "pairs", "finite_pairs", "equal_finite",
              "equal_stored_bits", "nonfinite_class_mismatches", "failed", "min_signed", "max_signed", "max_abs",
              "max_tolerance_fraction", "A_finite_min", "A_finite_max", "candidate_finite_min",
              "candidate_finite_max", "excluded_A", "A_oov", "candidate_oov", "source_branch", "population")
    with (output / "all-state-object-summary.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        for label, bundle in bundles.items():
            for state in bundle["states"]:
                for name, variable in bundle["quantities"].items():
                    row = record(bundle, name, state)
                    writer.writerow({
                        "candidate": label, "state": state, "variable": name,
                        "effective_state": variable["effective_state"][state],
                        "unit": variable["unit"], "source_branch": variable["branch"], "population": variable["population"],
                        **{key: row.get(key) for key in ("excluded_A", "A_oov", "candidate_oov")},
                        **row["summary"],
                    })
    with (output / "all-state-category-counts.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("candidate", "state", "variable", "effective_state", "side", "category", "muons"))
        for label, bundle in bundles.items():
            for state in bundle["states"]:
                for name, variable in bundle["quantities"].items():
                    categories = record(bundle, name, state).get("categories")
                    if categories:
                        for side, counts in categories.items():
                            for key, count in counts.items():
                                writer.writerow((label, state, name, variable["effective_state"][state],
                                                 side, category_label(key, variable["kind"]), count))
    with (output / "all-state-endpoint-summary.csv").open("w", encoding="utf-8", newline="") as stream:
        columns = ("candidate", "state", "storage", "max_abs_difference", "A_underflow", "A_overflow",
                   "candidate_underflow", "candidate_overflow", "bins_differing")
        writer = csv.writer(stream)
        writer.writerow(columns)
        for label, bundle in bundles.items():
            for state in bundle["states"]:
                payload = bundle["endpoint"]["records"][state]
                for key in ("count", "sumw", "sumw2"):
                    a, c = [np.asarray(payload[side][key]) for side in ("A", "candidate")]
                    writer.writerow((label, state, key, np.max(np.abs(c - a), initial=0),
                                     a[0], a[-1], c[0], c[-1], np.count_nonzero(c != a)))


def main(
    plot_data: Annotated[list[Path], typer.Argument(help="One or both extracted A/B and A/C .json.gz bundles.")],
    output: Annotated[Path, typer.Option(help="Fresh directory for figures and plot indices.")],
    detail_all: Annotated[bool, typer.Option(help="Render detailed distributions for every state.")] = False,
):
    """Render report panels and a complete variable/state appendix from observed results."""
    bundles = read_bundles(plot_data)
    output.mkdir(parents=True, exist_ok=False)
    write_summary(output, bundles)
    first = next(iter(bundles.values()))
    quantities = first["quantities"]
    continuous = [name for name, spec in quantities.items() if spec["axis"]]
    discrete = [name for name, spec in quantities.items() if not spec["axis"]]
    pages = []
    standalone = []

    with PdfPages(output / "agreement-appendix.pdf") as pdf:
        def page(figure, title, standalone_name=None):
            pdf.savefig(figure)
            pages.append(title)
            if standalone_name:
                for extension in ("svg", "png"):
                    figure.savefig(output / f"{standalone_name}.{extension}", dpi=220)
                standalone.append((standalone_name, title))
            plt.close(figure)

        page(representative_figure(bundles), "Nominal momentum and object weight", "agreement-main")
        page(threshold_figure(bundles), "Decisions near eta and pT cuts", "agreement-thresholds")
        page(endpoint_figure(bundles, "NOSYS"), "Nominal weighted pT endpoint", "agreement-endpoint")
        page(endpoint_systematics(bundles), "Weighted endpoint across all states", "agreement-endpoint-states")
        for label, bundle in bundles.items():
            for start in range(0, len(bundle["states"]), 20):
                subset = bundle["states"][start:start+20]
                name = f"agreement-states-{label.lower()}-{start//20+1}"
                page(state_map(bundle, subset, start), f"{label} / A states {start+1}–{start+len(subset)}", name)
        for state in detailed_states(bundles, continuous, detail_all):
            for name in continuous:
                fig = continuous_figure(bundles, name, state)
                standalone_name = f"agreement-{name.replace('_', '-')}" if state == "NOSYS" else None
                page(fig, f"{quantities[name]['title']} · {state_label(state)}", standalone_name)
                if state == "NOSYS" and "core" in quantities[name]["axis"]:
                    page(continuous_figure(bundles, name, state, "core"), f"{quantities[name]['title']} · Nominal core")
        for state in detailed_states(bundles, discrete, detail_all):
            for index in range(0, len(discrete), 6):
                group = discrete[index:index+6]
                standalone_name = f"agreement-decisions-{index//6+1}" if state == "NOSYS" else None
                page(discrete_figure(bundles, group, state), f"Discrete decisions {index//6+1} · {state_label(state)}", standalone_name)

    with (output / "figure-index.md").open("w", encoding="utf-8") as stream:
        stream.write("# Muon agreement figures\n\n" + plot_meta(bundles) + "\n\n")
        stream.write("[All figures and state summaries as PDF](agreement-appendix.pdf) · ")
        stream.write("[Object comparison tables](all-state-object-summary.csv) · ")
        stream.write("[Discrete category counts](all-state-category-counts.csv) · ")
        stream.write("[Endpoint comparison tables](all-state-endpoint-summary.csv)\n\n")
        for name, title in standalone:
            stream.write(f"- {title} · [SVG]({name}.svg) · [PNG]({name}.png)\n")
        stream.write("\n## Appendix pages\n\n")
        for index, title in enumerate(pages, 1):
            stream.write(f"{index}. {title}\n")
    provenance = {
        "input_bundles": [{"path": str(path.resolve()), "sha256": hash_path(path)} for path in plot_data],
        "source_data": {label: data["provenance"] for label, data in bundles.items()},
        "implementation_evidence": {label: data.get("implementation_evidence", []) for label, data in bundles.items()},
        "renderer_sha256": hash_path(Path(__file__)),
        "matplotlib_version": matplotlib.__version__, "numpy_version": np.__version__,
    }
    (output / "figure-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")


def hash_path(path):
    with path.open("rb") as file:
        return file_digest(file, "sha256").hexdigest()


if __name__ == "__main__":
    typer.run(main)
