# A Dual-Core RTOS Scheduler Inference Framework
### Code for the paper "An Efficient Inference Framework for Scheduler Side-Channel Analysis in Multi-Core RTOS"

simulation/
- clean_data.py  `check and delete logs without preemption`
- parsing.py  `create samples based on attacker’s view`
- simulator.py – `simulate the runtime under different settings`
- task_configs.json `task sets with utilization of 0.9-1.0`
- task_configs_l.json `task sets with utilization of 1.1-1.2`

inference/
- assessment.py `Schedule reconstruction and evaluation utilities.`
> python assessment.py --results_dir results/
- baseline.py `The entry point of RTOS scheduler inference.`
>  There are four modes as follows:<br>
    python baseline.py --mode eval    --csv_dir log_attacker/ --with-ca --use-knowledge<br>
    python baseline.py --mode stats   --csv_dir log_attacker/ --folds 10 --with-ca<br>
    python baseline.py --mode predict --csv_dir log_attacker/ --output_dir results/<br>
    python baseline.py --mode profile --csv_dir log_attacker/ --with-ca --use-knowledge
- graphs.py `Significance test of the scheduler reconstruction results`
- prediction.py `Train the CA-reservoir (best-performing model, knowledge=ON) with 10-fold CV
and save one prediction CSV per validation fold, compatible with assessment.py`
- processing.py `Data parsing and encoding utilities for the RTOS scheduler inference pipeline.`
- profiling.py `Model-size and per-sample inference-latency profiler for all baselines.`
> Usage:<br>
    python profiling.py [flags]<br>
    Via baseline:  python baseline.py --mode profile [flags]

am64x/
- Port of the scheduler-latency experiment to the TI SK-AM64 (AM6442)
  starter kit. Two lines:
  - `am64x/a53/` **(primary)** — dual Cortex-A53 running **FreeRTOS SMP**. 
    One scheduler across two cache-coherent cores, which is the configuration 
    the multi-core threat model actually needs. Adds task-migration
    observation; every record is attributed to the core that produced it.
  - `am64x/r5f/` (fallback) — single Cortex-R5F, closest analogue to the
    STM32WB55 capture.
- A53 line builds end-to-end with MCU+ SDK 11.01 (CCS `.out` and SBL-loadable
  signed appimage). R5F line compiles.

log_full/

### Note:
+ AM64 implementation is only needed for overhead measurement. The number will differ across different hardwares.
+ It is essential to run the simulation first, then store the parsed log file in the **log_full** folder.

