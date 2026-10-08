import re
import pandas as pd
import  csv
import re

def get_attacker_view(full_log_filename, timestamp_divisor=1_000_000.0):
    results = []
    current_start = None
    current_core = None

    with open(full_log_filename, mode='r') as file:
        reader = csv.DictReader(file)
        for row in reader:
            ts = int(row["Timestamp_ms"]) / timestamp_divisor
            text = row["Task_Log"]

            if "Observer" not in text:
                continue

            if "Executing" in text:
                current_start = ts
                match = re.search(r'Core (\d+)', text)
                if match:
                    current_core = int(match.group(1))

            elif ("Terminated" in text or "Preempted" in text) and current_start is not None:
                results.append({
                    "start_ts": current_start,
                    "end_ts": ts,
                    #"core": current_core
                })
                current_start = None
                current_core = None

    #return pd.DataFrame(results, columns=["start_ts", "end_ts", "core"])
    return pd.DataFrame(results, columns=["start_ts", "end_ts"])

