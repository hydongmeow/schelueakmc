"""
There are 4 modalities for input and 1 output:
1. S - Time series from CSV files
2. s - Scheduler type from filename (categorical)
3. o - Observer task configuration (JSON → encoded)
4. T - Critical tasks partial config (JSON → encoded)
5. Output - Period values for each task identifier

parse_variation_from_config,
    parse_scheduler_from_filename,
    encode_sample_from_csv,
    SCHEDULER_VOCAB,
"""
import torch
import numpy as np
import pandas as pd
import os
import io
import re
import glob
from torch import nn
from pathlib import Path
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Literal
from torch.utils.data import Dataset, DataLoader


# 1. S - Time series from CSV files
def print_temporal_summary(model: nn.Module, input_shape: tuple, device='cpu'):
    """
    Print parameter count, model size, and FLOPs for the given model and input shape.

    Args:
        model: The PyTorch model.
        input_shape: Tuple (batch_size, seq_len, input_channels) for a dummy forward pass.
        device: Device to run the dummy input on.
    """
    # Parameters
    total_params = sum(p.numel() for p in model.parameters())
    trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)

    # Model size (state_dict serialized to bytes)
    buffer = io.BytesIO()
    torch.save(model.state_dict(), buffer)
    size_bytes = buffer.tell()
    size_mb = size_bytes / (1024 ** 2)

    # FLOPs using thop (if available)
    try:
        from thop import profile
        dummy_input = torch.randn(*input_shape).to(device)
        model.to(device)
        model.eval()
        flops, _ = profile(model, inputs=(dummy_input,), verbose=False)
        flops_str = f"{flops:,}"
    except ImportError:
        flops_str = "thop not installed, cannot compute FLOPs"

    print(f"Total parameters:    {total_params:,}")
    print(f"Trainable parameters:{trainable_params:,}")
    print(f"Model size (state_dict): {size_mb:.2f} MB")
    print(f"FLOPs (input shape {input_shape}): {flops_str}")


#2. s - Scheduler type from filename (categorical)
SCHEDULER_VOCAB: Dict[str, int] = {
    "GFLZL": 0,
    "LLF":     1,
    "EDZL":    2,
    "EDCL":    3,
    "MLLF":    4,
    "RUN":     5,
    "WCRUN":  6,
    "GFL":    7,
    "EDF":     8,
    "NVNLF":   9,
}


class SchedulerEncoder(nn.Module):
    """
    Encodes the scheduler type (a categorical string) into a dense embedding.

    The scheduler label is parsed directly from the CSV filename:
        attacker_<SCHEDULER>_variation_...csv
    """

    def __init__(self, vocab: Dict[str, int], embed_dim: int = 16):
        super().__init__()
        self.vocab = vocab
        self.embedding = nn.Embedding(
            num_embeddings=len(vocab),
            embedding_dim=embed_dim,
            padding_idx=None,
        )

    def forward(self, scheduler_ids: torch.Tensor) -> torch.Tensor:
        """
        Args:
            scheduler_ids: (batch,) long tensor of scheduler indices
        Returns:
            embedding: (batch, embed_dim)
        """
        return self.embedding(scheduler_ids)

def parse_scheduler_from_filename(filename: str, vocab: Dict[str, int]) -> int:
    """
    Extracts the scheduler token from a filename and returns its integer index.

    Pattern: attacker_<SCHEDULER>_variation_...

    Args:
        filename: basename of the CSV file (with or without path)
        vocab:    mapping from scheduler name → integer index
    Returns:
        integer index of the scheduler
    Raises:
        ValueError if the scheduler token is not found in vocab
    """
    basename = Path(filename).stem          # strip directory + extension
    match    = re.match(r"attacker_([^_]+)_variation", basename)
    if match is None:
        raise ValueError(f"Cannot parse scheduler from filename: '{filename}'")

    scheduler_str = match.group(1)
    if scheduler_str not in vocab:
        raise ValueError(
            f"Scheduler '{scheduler_str}' not in vocab {list(vocab.keys())}"
        )
    return vocab[scheduler_str]

"""
3. o - Observer task configuration (JSON → encoded)
4. T - Critical tasks partial config (JSON → encoded)
5. Output - Period values for each task identifier
"""

@dataclass
class ObserverField:
    """Parsed fields from the observer task (identifier=4)."""
    wcet:       float
    identifier: int
    period:     float

@dataclass
class CriticalTask:
    """A single critical task (identifier 1, 2, or 3)."""
    identifier: int
    wcet:       float

@dataclass
class OutputTarget:
    """Target output: period for each critical task, keyed by identifier."""
    periods: Dict[int, float]  # {identifier: period}

class ObserverFieldEncoder:
    """
    Encodes ObserverField into a fixed-length numeric vector.

    Encoding:
        [wcet, identifier]

    identifier is always 4 for the observer task.
    No vocabulary lookup needed — the integer is used directly.
    """

    OBSERVER_IDENTIFIER: int = 4

    def __init__(self) -> None:
        pass   # no vocab to build anymore

    def encode(self, obs: ObserverField) -> np.ndarray:
        if obs.identifier != self.OBSERVER_IDENTIFIER:
            raise ValueError(
                f"Expected observer identifier={self.OBSERVER_IDENTIFIER}, "
                f"got {obs.identifier}"
            )
        return np.array([obs.wcet, float(obs.identifier),  float(obs.period)], dtype=np.float32)

    @property
    def feature_dim(self) -> int:
        return 3


class CriticalTaskEncoder:
    """
    Encodes a list of CriticalTask objects into a fixed-length numeric vector.

    Tasks are sorted by identifier (1, 2, 3) so the order is always stable.
    Each task contributes [identifier, wcet].

    Final vector length = num_tasks * 2.
    """

    EXPECTED_IDENTIFIERS: Tuple[int, ...] = (1, 2, 3)

    def encode(self, tasks: List[CriticalTask]) -> np.ndarray:
        task_map: Dict[int, CriticalTask] = {t.identifier: t for t in tasks}

        parts: List[float] = []
        for ident in self.EXPECTED_IDENTIFIERS:
            if ident not in task_map:
                raise ValueError(f"Missing critical task with identifier={ident}")
            t = task_map[ident]
            parts.extend([float(t.identifier), t.wcet])

        return np.array(parts, dtype=np.float32)

    @property
    def feature_dim(self) -> int:
        return len(self.EXPECTED_IDENTIFIERS) * 2


class OutputEncoder:
    """
    Encodes OutputTarget (periods keyed by identifier) into a fixed-length vector.

    Tasks are sorted by identifier (1, 2, 3).
    Final vector length = num_tasks.
    """

    EXPECTED_IDENTIFIERS: Tuple[int, ...] = (1, 2, 3)

    def encode(self, output: OutputTarget) -> np.ndarray:
        parts: List[float] = []
        for ident in self.EXPECTED_IDENTIFIERS:
            if ident not in output.periods:
                raise ValueError(f"Missing period for identifier={ident}")
            parts.append(float(output.periods[ident]))
        return np.array(parts, dtype=np.float32)

    def decode(self, vector: np.ndarray) -> OutputTarget:
        if len(vector) != len(self.EXPECTED_IDENTIFIERS):
            raise ValueError("Vector length mismatch during decode.")
        return OutputTarget(
            periods={ident: float(vector[i])
                     for i, ident in enumerate(self.EXPECTED_IDENTIFIERS)}
        )

    @property
    def feature_dim(self) -> int:
        return len(self.EXPECTED_IDENTIFIERS)

_VARIATION_RE = re.compile(r"variation_(\d+)", re.IGNORECASE)

OBSERVER_IDENTIFIER = 4
CRITICAL_IDENTIFIERS = {1, 2, 3}


def _extract_variation_number(csv_filename: str) -> int:
    basename = os.path.basename(csv_filename)
    match = _VARIATION_RE.search(basename)
    if match is None:
        raise ValueError(
            f"Cannot extract variation number from filename: '{basename}'"
        )
    return int(match.group(1))


def parse_variation_from_config(
    config_path: str,
    csv_filename: str,
) -> Tuple[ObserverField, List[CriticalTask], OutputTarget]:
    """
    Given a config.json path and a CSV filename, return:
        - ObserverField  (from observer_task, identifier=4)
        - List[CriticalTask]  (tasks with identifiers 1, 2, 3)
        - OutputTarget  (periods for identifiers 1, 2, 3)

    Steps
    -----
    1. Extract variation number from the CSV filename.
    2. Load config.json and locate the matching variation block.
    3. Parse observer_task  →  ObserverField.
    4. Parse tasks          →  List[CriticalTask] + OutputTarget.
    """

    # ── 1. variation number ──────────────────────────────────────────────
    variation_number = _extract_variation_number(csv_filename)
    variation_key    = f"variation_{variation_number}"

    # ── 2. load config ───────────────────────────────────────────────────
    with open(config_path, "r") as fh:
        config: dict = json.load(fh)

    variations: dict = config.get("variations", {})
    if variation_key not in variations:
        raise KeyError(
            f"Variation '{variation_key}' not found in config. "
            f"Available: {list(variations.keys())}"
        )
    variation_data: dict = variations[variation_key]

    # ── 3. observer task (identifier must be 4) ──────────────────────────
    obs_raw = variation_data["observer_task"]
    if obs_raw["identifier"] != OBSERVER_IDENTIFIER:
        raise ValueError(
            f"Expected observer identifier={OBSERVER_IDENTIFIER}, "
            f"got {obs_raw['identifier']}"
        )

    observer_field = ObserverField(
        identifier = int(obs_raw["identifier"]),
        wcet     = float(obs_raw["wcet"]),
        period     = float(obs_raw["period"])
    )

    # ── 4. critical tasks (identifiers 1, 2, 3) ──────────────────────────
    tasks_raw: List[dict] = variation_data["tasks"]

    critical_tasks: List[CriticalTask] = []
    output_periods: Dict[int, float]   = {}

    for t in tasks_raw:
        ident = int(t["identifier"])
        if ident not in CRITICAL_IDENTIFIERS:
            continue                        # skip unexpected identifiers

        critical_tasks.append(
            CriticalTask(identifier=ident, wcet=float(t["wcet"]))
        )
        output_periods[ident] = float(t["period"])

    missing = CRITICAL_IDENTIFIERS - {ct.identifier for ct in critical_tasks}
    if missing:
        raise ValueError(f"Missing critical task identifiers: {missing}")

    output_target = OutputTarget(periods=output_periods)

    return observer_field, critical_tasks, output_target



# Convenience: encode everything

def encode_sample_from_csv(
    config_path: str,
    csv_filename: str,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    High-level helper.

    Returns
    -------
    features : np.ndarray  shape (observer_dim + task_dim,)
    targets  : np.ndarray  shape (output_dim,)
    """
    observer_field, critical_tasks, output_target = parse_variation_from_config(
        config_path, csv_filename
    )

    obs_enc    = ObserverFieldEncoder()
    task_enc   = CriticalTaskEncoder()
    output_enc = OutputEncoder()

    features = np.concatenate([
        obs_enc.encode(observer_field),
        task_enc.encode(critical_tasks),
    ])
    targets = output_enc.encode(output_target)

    return features, targets

