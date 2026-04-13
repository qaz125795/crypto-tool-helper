# SPDX-License-Identifier: MIT
# 改編自 https://github.com/StephanAkkerman/liquidations-chart
import logging
import glob
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from io import BytesIO
from xml.etree import ElementTree

import requests
from tqdm import tqdm

logger = logging.getLogger(__name__)


def get_existing_files(symbol: str = "BTCUSDT") -> list[str]:
    response = requests.get(
        f"https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?delimiter=/&prefix=data/futures/um/daily/liquidationSnapshot/{symbol}/",
        timeout=60,
    )
    response.raise_for_status()
    tree = ElementTree.fromstring(response.content)

    files = []
    for content in tree.findall("{http://s3.amazonaws.com/doc/2006-03-01/}Contents"):
        key = content.find("{http://s3.amazonaws.com/doc/2006-03-01/}Key")
        if key is not None and key.text and key.text.endswith(".zip"):
            files.append(key.text)

    return files


def extract_date_from_filename(filename: str) -> str:
    return filename.split("liquidationSnapshot-")[-1].split(".")[0]


def get_local_dates(base_path: str, symbol: str, market: str):
    path_pattern = os.path.join(base_path, symbol, market, "*.csv")
    local_files = glob.glob(path_pattern)
    local_dates = {
        extract_date_from_filename(os.path.basename(file)) for file in local_files
    }
    return local_dates


def download_and_extract_zip(
    symbol: str,
    date: datetime,
    market: str = "um",
    base_extract_to: str = "./data",
):
    os.makedirs(base_extract_to, exist_ok=True)

    extract_to = os.path.join(base_extract_to, symbol)
    os.makedirs(extract_to, exist_ok=True)

    extract_to = os.path.join(extract_to, market)
    os.makedirs(extract_to, exist_ok=True)

    date_str = date.strftime("%Y-%m-%d")
    url = f"https://data.binance.vision/data/futures/{market}/daily/liquidationSnapshot/{symbol}/{symbol}-liquidationSnapshot-{date_str}.zip"

    try:
        response = requests.get(url, timeout=120)
        response.raise_for_status()

        with zipfile.ZipFile(BytesIO(response.content)) as zip_ref:
            zip_ref.extractall(extract_to)

    except requests.RequestException as e:
        logger.debug("Failed to download %s: %s", url, e)
    except zipfile.BadZipFile as e:
        logger.debug("Failed to extract %s: %s", url, e)


def get_new_data(
    symbol: str,
    market: str = "um",
    base_extract_to: str = "./data",
    max_sync_days: int = 14,
) -> set[str]:
    """
    同步 Binance 公開清算快照。僅補齊「最近 max_sync_days 天內」尚未下載的日期，
    避免首次執行嘗試抓滿歷史導致極久下載。
    """
    existing_files = get_existing_files(symbol)
    existing_dates = {extract_date_from_filename(file) for file in existing_files}

    local_dates = get_local_dates(base_extract_to, symbol, market)
    missing_dates = existing_dates - local_dates

    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_sync_days)).strftime("%Y-%m-%d")
    missing_dates = {d for d in missing_dates if d >= cutoff}

    if not missing_dates:
        return set()

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(
                download_and_extract_zip,
                symbol,
                datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc),
                market,
                base_extract_to,
            )
            for date in sorted(missing_dates)
        ]
        for future in tqdm(
            as_completed(futures),
            total=len(futures),
            desc=f"Downloading {symbol} liquidations",
        ):
            try:
                future.result()
            except Exception as e:
                logger.debug("Download worker error: %s", e)

    return missing_dates
