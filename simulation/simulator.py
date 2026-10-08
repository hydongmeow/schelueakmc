import simso
from simso.core import Model
from simso.configuration import Configuration
from simso.schedulers import G_FL , G_FL_ZL, EDF, LLF, EDZL, EDCL, EDF, EKG, EKG, MLLF, NVNLF, RUN,WC_RUN
import pandas as pd
import csv
from datetime import datetime
import json
from parsing import get_attacker_view
import time

def load_task_config(config_path="task_configs.json"):
    """Load task configurations from a JSON file."""
    try:
        with open(config_path, "r") as f:
            print("Data Loaded from JSON file.")
            return json.load(f)
    except FileNotFoundError:
        print(f"[ERROR] The file '{config_path}' does not exist.")
        return None
    except json.JSONDecodeError as e:
        print(f"[ERROR] Failed to parse '{config_path}': {e}")
        return None


def run_2core_global_simulation(
    scheduler_name="EDF",
    sim_time_ms=5000,
    variation="variation_1_high_frequency",
    config_path="task_configs.json"
):
    """
    Simulate a 2-core RTOS with global scheduling.
    """
    # 1. Create configuration
    config = Configuration()

    # SimSo uses cycles for duration. Default cycles_per_ms is 1,000,000.
    config.duration = sim_time_ms * config.cycles_per_ms

    # 2. Add Processors (Cores)
    config.add_processor(name="Core 1", identifier=1)
    config.add_processor(name="Core 2", identifier=2)

    # 3. Add Scheduler
    # For built-in schedulers, you pass the path to the class
    config.scheduler_info.clas ="simso.schedulers." + scheduler_name

    # 4. Define tasks (period, wcet, deadline in ms)
    task_data = load_task_config(config_path)
    observer_task = task_data["variations"][variation]["observer_task"]
    selected_variation = task_data["variations"][variation]
    regular_tasks = selected_variation["tasks"]
    for task in regular_tasks:
        config.add_task(
            name       = task["name"],
            identifier = task["identifier"],
            period     = task["period"],
            wcet       = task["wcet"],
            deadline   = task["deadline"],
        )
        print(
            f"  [Task Added] {task['name']:12s} | "
            f"Period={task['period']:>4} ms | "
            f"WCET={task['wcet']:>3} ms | "
            f"Deadline={task['deadline']:>4} ms"
        )
    config.add_task(
        name=observer_task["name"],
        identifier=observer_task["identifier"],
        period=observer_task["period"],
        wcet=observer_task["wcet"],
        deadline=observer_task["deadline"],
    )
    print(
        f"  [Observer  ] {observer_task['name']:12s} | "
        f"Period={observer_task['period']:>4} ms | "
        f"WCET={observer_task['wcet']:>3} ms | "
        f"Deadline={observer_task['deadline']:>4} ms"
    )

    # 5. Check configuration correctness
    config.check_all()

    # 6. Initialize the Model and run the simulation
    model = Model(config)
    model.run_model()
    # 7. Save the raw log.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    full_log_filename = f"full_system_{scheduler_name}_{variation}_{timestamp}.csv"

    with open(full_log_filename, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Timestamp_ms", "Task_Log"])

        for log in model.logs:
            if isinstance(log, tuple) or isinstance(log, list):
                writer.writerow(list(log))
            else:
                row_data = str(log).split()
                writer.writerow(row_data)

    print(f"[CSV 1] Full log saved to    : {full_log_filename}")
    time.sleep(3)

    print(pd.read_csv(full_log_filename).head())
    attacker_data = get_attacker_view(full_log_filename)
    attacker_filename = f"attacker_{scheduler_name}_{variation}_{timestamp}.csv"
    attacker_data.to_csv(attacker_filename, index=False)
    """
    with open(attacker_filename, mode='w', newline='') as attacker_file:
        writer = csv.writer(attacker_file)
        writer.writerow(["Release_ms", "Finish_ms", "Core_ID"])
        for row in attacker_data:
            writer.writerow(row)
    """
    print(f"[CSV 2] Attacker data saved to: {attacker_filename}")

    return model, attacker_data



# Example usage
if __name__ == "__main__":

    schedulers = ['G_FL_ZL', 'LLF', 'EDZL', 'EDCL', 'MLLF', 'RUN', 'WC_RUN', 'G_FL', 'EDF', 'NVNLF']
    SIM_TIME_MS = 5000

    for SCHEDULER in schedulers:
        task_data = load_task_config("task_configs.json")
        variations = list(task_data["variations"].keys())
        for var in variations:
            model, attacker_data = run_2core_global_simulation(
                scheduler_name = SCHEDULER,
                sim_time_ms    = SIM_TIME_MS,
                variation      = var,
            )
    print("\n\n=== Simulation Complete ===")
    #print(json.dumps(all_results, indent=2))
    #print(attacker_data)