"""
processing.py
=============
Data parsing and encoding utilities for the RTOS scheduler inference pipeline.

Exported symbols used by the rest of the package:
    SCHEDULER_VOCAB              dict[str, int]
    parse_scheduler_from_filename(filename, vocab) -> int
    parse_variation_from_config(config_path, csv_filename)
        -> (ObserverField, List[CriticalTask], OutputTarget)
    encode_sample_from_csv(config_path, csv_filename)
        -> (features: ndarray (9,), targets: ndarray (3,))

Four modalities defined in the dataset:
    S  - Time-series execution trace (CSV columns start_ts / end_ts)
    s  - Scheduler type parsed from filename  (categorical → one-hot in baseline)
    o  - Observer task configuration  (wcet, identifier, period)
    T  - Critical tasks partial config (identifier, wcet for tasks 1-3)
    Output - Period for each critical task (identifiers 1, 2, 3)
"""

import re
import os
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np


# ============================================================================
# Scheduler vocabulary  (modality s)
# ============================================================================
SCHEDULER_VOCAB: Dict[str, int] = {
    "GFLZL": 0,
    "LLF":   1,
    "EDZL":  2,
    "EDCL":  3,
    "MLLF":  4,
    "RUN":   5,
    "WCRUN": 6,
    "GFL":   7,
    "EDF":   8,
    "NVNLF": 9,
}


def parse_scheduler_from_filename(filename: str, vocab: Dict[str, int]) -> int:
    """
    Extract the scheduler token from a filename and return its integer index.
    Pattern: attacker_<SCHEDULER>_variation_...
    """
    basename = Path(filename).stem
    match = re.match(r"attacker_([^_]+)_variation", basename)
    if match is None:
        raise ValueError(f"Cannot parse scheduler from filename: '{filename}'")
    token = match.group(1)
    if token not in vocab:
        raise ValueError(f"Scheduler '{token}' not in vocab {list(vocab.keys())}")
    return vocab[token]


# ============================================================================
# Dataclasses  (modalities o, T and output)
# ============================================================================
@dataclass
class ObserverField:
    """Observer task fields (identifier always = 4)."""
    wcet:       float
    identifier: int
    period:     float


@dataclass
class CriticalTask:
    """One critical task (identifier 1, 2, or 3)."""
    identifier: int
    wcet:       float


@dataclass
class OutputTarget:
    """Target periods keyed by task identifier."""
    periods: Dict[int, float]   # {1: T1, 2: T2, 3: T3}


# ============================================================================
# Config parsing
# ============================================================================
_VARIATION_RE      = re.compile(r"variation_(\d+)", re.IGNORECASE)
_OBSERVER_ID       = 4
_CRITICAL_IDS      = {1, 2, 3}


def parse_variation_from_config(
    config_path: str,
    csv_filename: str,
) -> Tuple[ObserverField, List[CriticalTask], OutputTarget]:
    """
    Parse one variation block from task_configs.json for the given CSV file.

    Returns
    -------
    observer      : ObserverField
    critical_tasks: List[CriticalTask]   (identifiers 1, 2, 3)
    output_target : OutputTarget          (periods for identifiers 1, 2, 3)
    """
    # variation number from filename
    basename = os.path.basename(csv_filename)
    match = _VARIATION_RE.search(basename)
    if match is None:
        raise ValueError(f"Cannot extract variation number from: '{basename}'")
    key = f"variation_{match.group(1)}"

    with open(config_path) as fh:
        cfg = json.load(fh)

    variations = cfg.get("variations", {})
    if key not in variations:
        raise KeyError(f"'{key}' not in config. Available: {list(variations.keys())}")
    vdata = variations[key]

    # observer
    obs_raw = vdata["observer_task"]
    if int(obs_raw["identifier"]) != _OBSERVER_ID:
        raise ValueError(f"Expected observer id={_OBSERVER_ID}, got {obs_raw['identifier']}")
    observer = ObserverField(
        wcet=float(obs_raw["wcet"]),
        identifier=int(obs_raw["identifier"]),
        period=float(obs_raw["period"]),
    )

    # critical tasks
    critical_tasks, periods = [], {}
    for t in vdata["tasks"]:
        ident = int(t["identifier"])
        if ident not in _CRITICAL_IDS:
            continue
        critical_tasks.append(CriticalTask(identifier=ident, wcet=float(t["wcet"])))
        periods[ident] = float(t["period"])

    missing = _CRITICAL_IDS - {ct.identifier for ct in critical_tasks}
    if missing:
        raise ValueError(f"Missing critical task identifiers: {missing}")

    return observer, critical_tasks, OutputTarget(periods=periods)


# ============================================================================
# Convenience encoder
# ============================================================================
def encode_sample_from_csv(
    config_path: str,
    csv_filename: str,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Encode one sample into flat numeric arrays.

    Returns
    -------
    features : ndarray shape (9,)
        [obs_wcet, obs_id, obs_period,
         task1_id, task1_wcet,
         task2_id, task2_wcet,
         task3_id, task3_wcet]
    targets  : ndarray shape (3,)
        [period_1, period_2, period_3]
    """
    observer, tasks, output = parse_variation_from_config(config_path, csv_filename)

    # observer block  (3 values)
    obs_vec = np.array([observer.wcet, float(observer.identifier), observer.period],
                       dtype=np.float32)

    # critical tasks block  (2 values × 3 tasks = 6 values), sorted by id
    task_map = {t.identifier: t for t in tasks}
    task_vec = np.array(
        [v for ident in (1, 2, 3) for v in (float(task_map[ident].identifier),
                                              task_map[ident].wcet)],
        dtype=np.float32,
    )

    # targets  (3 values)
    target_vec = np.array([output.periods[i] for i in (1, 2, 3)], dtype=np.float32)

    return np.concatenate([obs_vec, task_vec]), target_vec
