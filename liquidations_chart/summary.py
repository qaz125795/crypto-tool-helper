# SPDX-License-Identifier: MIT
# 改編自 https://github.com/StephanAkkerman/liquidations-chart
import glob
import os
from datetime import datetime, timezone

import pandas as pd


def convert_timestamp_to_date(timestamp):
    return datetime.utcfromtimestamp(timestamp / 1000).strftime("%Y-%m-%d")


def summarize_liquidations(coin="BTCUSDT", market="um", base_dir="./data"):
    file_pattern = os.path.join(base_dir, coin, market, "*.csv")
    all_files = glob.glob(file_pattern)

    if not all_files:
        raise FileNotFoundError(f"No liquidation CSV under {file_pattern}")

    df_list = []
    for file in all_files:
        df = pd.read_csv(file)
        df_list.append(df)

    all_data = pd.concat(df_list, ignore_index=True)

    all_data.drop_duplicates(inplace=True)

    all_data["date"] = all_data["time"].apply(convert_timestamp_to_date)

    all_data["volume"] = all_data["original_quantity"] * all_data["average_price"]

    summary = (
        all_data.groupby(["date", "side"])
        .agg(
            total_volume=("volume", "sum"),
            total_liquidations=("original_quantity", "sum"),
        )
        .reset_index()
    )

    summary["average_price"] = summary["total_volume"] / summary["total_liquidations"]

    pivot_summary = summary.pivot(
        index="date", columns="side", values=["total_volume", "average_price"]
    ).fillna(0)
    pivot_summary.columns = [
        "_".join(col).strip() for col in pivot_summary.columns.values
    ]
    pivot_summary = pivot_summary.rename(
        columns={
            "total_volume_BUY": "Buy Volume (USD)",
            "total_volume_SELL": "Sell Volume (USD)",
            "average_price_BUY": "Average Buy Price",
            "average_price_SELL": "Average Sell Price",
        }
    )

    pivot_summary["Average Price"] = (
        pivot_summary["Average Buy Price"] * pivot_summary["Buy Volume (USD)"]
        + pivot_summary["Average Sell Price"] * pivot_summary["Sell Volume (USD)"]
    ) / (pivot_summary["Buy Volume (USD)"] + pivot_summary["Sell Volume (USD)"])

    pivot_summary.drop(
        columns=["Average Buy Price", "Average Sell Price"], inplace=True
    )

    pivot_summary.rename(
        columns={
            "Buy Volume (USD)": "Shorts",
            "Sell Volume (USD)": "Longs",
            "Average Price": "price",
        },
        inplace=True,
    )

    pivot_summary["date"] = pd.to_datetime(pivot_summary.index)
    pivot_summary = pivot_summary.set_index("date")

    out_dir = os.path.join(base_dir, "summary", coin, market)
    os.makedirs(out_dir, exist_ok=True)
    out_csv = os.path.join(out_dir, "liquidation_summary.csv")
    pivot_summary.to_csv(out_csv)
