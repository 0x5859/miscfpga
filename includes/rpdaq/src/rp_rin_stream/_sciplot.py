"""Scientific-plotting helpers vendored from the ``scientific-plotting`` skill.

Source: ``~/.claude/skills/scientific-plotting/scripts/plot_utils_reference.py``
plus ``references/figure-guidelines.md`` (the house rules). Trimmed to the
helpers actually used by :mod:`rin_cross_spectrum`. Keeping these vendored
in-tree means the project does not depend on the skill being installed
when someone clones the repo onto a fresh machine.

House rules implemented:
- Arial / Helvetica, 7 pt body text.
- Black 0.5 pt spines on all four sides.
- Tick marks inward, length 1.8 / width 0.5.
- Log axes: minor ticks at ``2..9 x 10^n``, hidden minor labels,
  length 1.2 / width 0.5; major grid ``--`` 0.25 / 0.5α gray; minor
  grid ``:`` 0.2 / 0.5α gray.
- No figure title by default (caption carries the narrative).
- Vector-friendly fonts (``pdf.fonttype = ps.fonttype = 42``,
  ``svg.fonttype = none``); 450 dpi raster.
- Fixed canvas with explicit ``axes_rect`` so the plotting box has the
  same physical size across re-runs.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence, Tuple


# Default plotting configuration. Override per-figure via ``resolve_cfg``.
PLOT_CFG: dict = {
    "font_name": "Arial",
    "font_size": 7,
    "axes_linewidth": 0.5,
    "tick_length": 1.8,
    "tick_direction": "in",
    "dpi": 450,
    "fig_width_cm": 12,
    "fig_height_cm": 6,
    "axes_rect": (0.15, 0.18, 0.82, 0.78),
    # Wong 2011 colour-blind safe palette
    "palette": [
        "#0072B2",  # blue
        "#D55E00",  # vermillion
        "#009E73",  # bluish-green
        "#CC79A7",  # reddish-purple
        "#56B4E9",  # sky blue
        "#E69F00",  # gold
        "#7A7A7A",  # gray
        "#000000",  # black
    ],
    "linewidth": 0.5,
    "grid": None,                        # auto-enable on log axes
    "gridlinestyle": "--",
    "gridlinewidth": 0.25,
    "gridcolor": "gray",
    "gridalpha": 0.5,
    "log_minor_subs": tuple(range(2, 10)),
    "log_minor_tick_length": 1.2,
    "log_minor_tick_width": 0.5,
    "log_minor_tick_color": "k",
    "log_minor_gridlinestyle": ":",
    "log_minor_gridlinewidth": 0.2,
    "log_minor_gridalpha": 0.5,
    "legend": False,
    "figure_facecolor": "white",
    "axes_facecolor": "white",
    "save_transparent": False,
    "bbox_inches": None,
    "pad_inches": 0.04,
    "pdf_fonttype": 42,
    "ps_fonttype": 42,
}


def resolve_cfg(overrides: Optional[dict] = None) -> dict:
    cfg = dict(PLOT_CFG)
    if overrides:
        cfg.update(overrides)
    return cfg


def _cm2inch(w_cm: float, h_cm: float) -> Tuple[float, float]:
    return w_cm / 2.54, h_cm / 2.54


def apply_plot_style(cfg: dict) -> None:
    """Apply matplotlib rcParams from ``cfg``. Idempotent."""
    import matplotlib.pyplot as plt
    from cycler import cycler

    fonts = [str(cfg.get("font_name", "Arial")), "Helvetica", "DejaVu Sans"]
    params = {
        "font.family": "sans-serif",
        "font.sans-serif": fonts,
        "font.size": cfg["font_size"],
        "axes.titlesize": cfg["font_size"],
        "axes.labelsize": cfg["font_size"],
        "axes.linewidth": cfg["axes_linewidth"],
        "axes.edgecolor": "k",
        "axes.labelcolor": "k",
        "xtick.color": "k",
        "ytick.color": "k",
        "xtick.direction": cfg.get("tick_direction", "in"),
        "ytick.direction": cfg.get("tick_direction", "in"),
        "xtick.major.size": cfg["tick_length"],
        "ytick.major.size": cfg["tick_length"],
        "xtick.major.width": cfg["axes_linewidth"],
        "ytick.major.width": cfg["axes_linewidth"],
        "xtick.labelsize": cfg["font_size"],
        "ytick.labelsize": cfg["font_size"],
        "legend.fontsize": cfg["font_size"],
        "legend.frameon": False,
        "savefig.dpi": cfg.get("dpi", 450),
        "pdf.fonttype": cfg.get("pdf_fonttype", 42),
        "ps.fonttype": cfg.get("ps_fonttype", 42),
        "svg.fonttype": "none",
        "lines.linewidth": cfg.get("linewidth", 0.5),
        "lines.solid_capstyle": "round",
        "figure.facecolor": cfg.get("figure_facecolor", "white"),
        "axes.facecolor": cfg.get("axes_facecolor", "white"),
        "axes.prop_cycle": cycler(color=cfg["palette"]),
    }
    plt.rcParams.update(params)


def _has_log_axis(ax) -> bool:
    return ax.get_xscale() == "log" or ax.get_yscale() == "log"


def _style_log_ticks_and_grid(ax, cfg: dict, *, grid: bool) -> None:
    from matplotlib.ticker import LogLocator, NullFormatter

    minor_subs = tuple(cfg.get("log_minor_subs", tuple(range(2, 10))))
    minor_color = cfg.get("log_minor_tick_color", "k")
    for axis_name, axis_obj, scale in (
        ("x", ax.xaxis, ax.get_xscale()),
        ("y", ax.yaxis, ax.get_yscale()),
    ):
        if scale != "log":
            continue
        axis_obj.set_minor_locator(LogLocator(base=10.0, subs=minor_subs))
        axis_obj.set_minor_formatter(NullFormatter())
        ax.tick_params(
            axis=axis_name,
            which="minor",
            direction=cfg.get("tick_direction", "in"),
            length=cfg.get("log_minor_tick_length", 1.2),
            width=cfg.get("log_minor_tick_width", 0.5),
            colors=minor_color,
        )
        if grid:
            ax.grid(
                True,
                axis=axis_name,
                which="minor",
                linestyle=cfg.get("log_minor_gridlinestyle", ":"),
                linewidth=cfg.get("log_minor_gridlinewidth", 0.2),
                color=cfg.get("gridcolor", "gray"),
                alpha=cfg.get("log_minor_gridalpha", 0.5),
            )
        else:
            ax.grid(False, axis=axis_name, which="minor")


def style_axes(
    ax,
    cfg: dict,
    *,
    grid: Optional[bool] = None,
    legend: Optional[bool] = None,
) -> None:
    """Apply axis styling without changing limits or data."""
    ax.tick_params(
        direction=cfg.get("tick_direction", "in"),
        length=cfg["tick_length"],
        width=cfg["axes_linewidth"],
        colors="k",
    )
    for spine in ax.spines.values():
        spine.set_linewidth(cfg["axes_linewidth"])
        spine.set_color("k")
        spine.set_visible(True)

    if grid is None:
        configured = cfg.get("grid", None)
        grid = _has_log_axis(ax) if configured is None else bool(configured)
    if grid:
        ax.grid(
            True, which="major",
            linestyle=cfg["gridlinestyle"],
            linewidth=cfg["gridlinewidth"],
            color=cfg.get("gridcolor", "gray"),
            alpha=cfg.get("gridalpha", 0.5),
        )
    else:
        ax.grid(False, which="both")

    _style_log_ticks_and_grid(ax, cfg, grid=grid)

    if legend:
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend()
    else:
        leg = ax.get_legend()
        if leg is not None:
            leg.remove()


def create_figure(
    cfg: Optional[dict] = None,
    *,
    size_cm: Optional[Tuple[float, float]] = None,
    axes_rect: Optional[Tuple[float, float, float, float]] = None,
):
    """Create a figure with one fixed-rect axes already styled."""
    import matplotlib.pyplot as plt

    cfg = resolve_cfg(cfg)
    apply_plot_style(cfg)

    width_cm, height_cm = size_cm or (cfg["fig_width_cm"], cfg["fig_height_cm"])
    fig = plt.figure(figsize=_cm2inch(width_cm, height_cm), dpi=cfg.get("dpi", 450))
    fig.patch.set_facecolor(cfg.get("figure_facecolor", "white"))
    rect = axes_rect or cfg["axes_rect"]
    ax = fig.add_axes(rect)
    ax.set_facecolor(cfg.get("axes_facecolor", "white"))
    style_axes(ax, cfg)
    return fig, ax, cfg


def add_axes(
    fig,
    cfg: dict,
    rect: Tuple[float, float, float, float],
):
    """Add a styled axes at the given fixed rectangle."""
    ax = fig.add_axes(rect)
    ax.set_facecolor(cfg.get("axes_facecolor", "white"))
    style_axes(ax, cfg)
    return ax


def save_figure(fig, path: Path, cfg: dict, *, dpi: Optional[int] = None) -> None:
    if dpi is None:
        dpi = int(cfg.get("dpi", 450))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        path,
        dpi=dpi,
        bbox_inches=cfg.get("bbox_inches", None),
        pad_inches=float(cfg.get("pad_inches", 0.04)),
        transparent=bool(cfg.get("save_transparent", False)),
    )


def save_figure_variants(fig, stem: Path, cfg: dict,
                         formats: Sequence[str] = ("pdf", "png")) -> List[Path]:
    paths = []
    for fmt in formats:
        p = stem.with_suffix(f".{fmt}")
        save_figure(fig, p, cfg)
        paths.append(p)
    return paths
