# SPDX-License-Identifier: MIT
# 改編自 https://github.com/StephanAkkerman/liquidations-chart
from datetime import timedelta
from math import floor, log

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib import ticker  # noqa: E402

BACKGROUND_COLOR = "#0d1117"
FIGURE_SIZE = (15, 7)
COLORS_LABELS = {"#d9024b": "Shorts", "#45bf87": "Longs", "#f0b90b": "Price"}


def human_format(number: float, absolute: bool = False, decimals: int = 0) -> str:
    if isinstance(number, str):
        try:
            number = float(number)
        except ValueError:
            number = 0

    if number == 0:
        return "0"

    units = ["", "K", "M", "B", "t", "q"]
    k = 1000.0
    magnitude = int(floor(log(abs(number), k)))

    if decimals > 0:
        rounded_number = round(number / k**magnitude, decimals)
    else:
        rounded_number = int(number / k**magnitude)

    if absolute:
        rounded_number = abs(rounded_number)

    return f"{rounded_number}{units[magnitude]}"


def add_legend(ax):
    legend_handles = [
        plt.Line2D(
            [0],
            [0],
            marker="s",
            color=BACKGROUND_COLOR,
            markerfacecolor=color,
            markersize=10,
            label=label,
        )
        for color, label in zip(
            list(COLORS_LABELS.keys()), list(COLORS_LABELS.values())
        )
    ]

    legend = ax.legend(
        handles=legend_handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.0),
        ncol=len(legend_handles),
        frameon=False,
        fontsize="small",
        labelcolor="white",
    )

    for text in legend.get_texts():
        text.set_fontweight("bold")

    plt.subplots_adjust(left=0.05, right=0.95, top=0.875, bottom=0.1)


def liquidations_plot(df, output_path=None, title_suffix: str = ""):
    """
    output_path: 若提供則寫入 PNG 並關閉 figure；否則行為同原專案（此處僅用於存檔）。
    """
    if df is None or df.empty:
        return False

    df_price = df[["price"]].copy()
    df_without_price = df.drop("price", axis=1)
    df_without_price["Shorts"] = df_without_price["Shorts"] * -1

    fig, ax1 = plt.subplots()
    fig.patch.set_facecolor(BACKGROUND_COLOR)
    ax1.set_facecolor(BACKGROUND_COLOR)

    ax2 = ax1.twinx()

    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    plt.gca().xaxis.set_major_locator(mdates.DayLocator(interval=14))

    ax1.bar(
        df_without_price.index,
        df_without_price["Shorts"],
        label="Shorts",
        color="#d9024b",
    )

    ax1.bar(
        df_without_price.index,
        df_without_price["Longs"],
        label="Longs",
        color="#45bf87",
    )

    ax1.get_yaxis().set_major_formatter(
        ticker.FuncFormatter(lambda x, _: f"${human_format(x, absolute=True)}")
    )

    ax2.plot(df_price.index, df_price, color="#edba35", label="BTC Price")
    ax2.set_xlim([df_price.index[0], df_price.index[-1]])
    ax2.set_ylim(bottom=df_price.min().values * 0.95, top=df_price.max().values * 1.05)
    ax2.get_yaxis().set_major_formatter(lambda x, _: f"${human_format(x)}")

    add_legend(ax2)

    plt.grid(axis="y", color="grey", linestyle="-.", linewidth=0.5, alpha=0.5)

    ax1.spines["top"].set_visible(False)
    ax1.spines["bottom"].set_visible(False)
    ax1.spines["right"].set_visible(False)
    ax1.spines["left"].set_visible(False)
    ax1.tick_params(left=False, bottom=False, right=False, colors="white")

    ax2.spines["top"].set_visible(False)
    ax2.spines["bottom"].set_visible(False)
    ax2.spines["right"].set_visible(False)
    ax2.spines["left"].set_visible(False)
    ax2.tick_params(left=False, bottom=False, right=False, colors="white")

    ax1.set_xlim(
        left=df_without_price.index[0] - timedelta(days=1),
        right=df_without_price.index[-1] + timedelta(days=1),
    )
    ax2.set_xlim(
        left=df_without_price.index[0] - timedelta(days=1),
        right=df_without_price.index[-1] + timedelta(days=1),
    )

    fig.set_size_inches(FIGURE_SIZE)

    title = "Total Liquidations Chart" + (f" {title_suffix}" if title_suffix else "")
    plt.text(
        -0.025,
        1.125,
        title,
        transform=ax1.transAxes,
        fontsize=14,
        verticalalignment="top",
        horizontalalignment="left",
        color="white",
        weight="bold",
    )

    if output_path:
        fig.savefig(output_path, dpi=120, facecolor=BACKGROUND_COLOR, bbox_inches="tight")
        plt.close(fig)
        return True
    plt.show()
    return True
