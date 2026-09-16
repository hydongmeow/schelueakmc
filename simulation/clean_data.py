import os
import glob
import pandas as pd
import re
import glob

def process_csv_files(folder_path):
    csv_files = glob.glob(os.path.join(folder_path, "*.csv"))

    for file_path in csv_files:
        file_name = os.path.basename(file_path)

        try:
            # Read CSV file
            df = pd.read_csv(file_path, sep=',')

            # (1) Calculate end_ts - start_ts for each row
            durations = (df['end_ts'] - df['start_ts']).tolist()

            # (2) Check if all values in the array are the same
            all_same = len(set(durations)) == 1

            # (3) If only one unique value exists, delete the file and warn
            if all_same:
                os.remove(file_path)
                print(f"[WARNING] '{file_name}' deleted — There is no preemption as all runrimes are ({durations[0]})")
            # else: do nothing, continue

        except KeyError:
            print(f"[ERROR] '{file_name}' does not have expected columns 'start_ts' and 'end_ts'")
        except Exception as e:
            print(f"[ERROR] Failed to process '{file_name}': {e}")

def fix_scheduler_names(data_dir: str) -> None:
    replacements = {
        "attacker_G_FL_ZL": "attacker_GFLZL",
        "attacker_G_FL":    "attacker_GFL",
        "attacker_WC_RUN":  "attacker_WCRUN",
    }
    for csv_path in glob.glob(os.path.join(data_dir, "*.csv")):
        basename = os.path.basename(csv_path)
        new_name = basename
        for old, new in replacements.items():
            if new_name.startswith(old):
                new_name = new_name.replace(old, new, 1)
                break

        if new_name != basename:
            new_path = os.path.join(data_dir, new_name)
            os.rename(csv_path, new_path)
            print(f"Renamed: {basename}  →  {new_name}")

if __name__ == "__main__":
    folder_path = "../log_attacker_l"
    process_csv_files(folder_path)
#    fix_scheduler_names(folder_path)
