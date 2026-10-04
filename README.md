# Two's a Crowd

Data and analysis for *Two's a Crowd: An Empirical Study of Conflicts Between Co-Installed Coding Agent Skills*.

## Run

Use Python 3.12. From this directory:

```sh
python3 -m pip install -r requirements.txt
python3 analyze.py
```

The analysis runs offline and writes `results.json` with prevalence estimates, RQ1–RQ3, the guard experiment, run counts, repeat agreement, and the Codex comparison. Differences are in percentage points. RQ1 averages paired model differences within each pair and uses pair-level t intervals. RQ3 uses pair-cluster bootstrap intervals with fixed seeds and Holm adjustment for the first-read comparisons.

## Files

| File | Contents |
|---|---|
| `data/cases.jsonl` | 312 pairs, skill identifiers, requests, applicable core functions, primary ranks, and exclusive/shared labels. |
| `data/runs.jsonl` | 5,698 run-level observations: 5,162 main runs and 536 additional repeats. |
| `data/first_read.jsonl` | 1,870 A+B and swapped run summaries: first-read timing, paired fidelity changes, and lost core-function counts. |
| `data/guard.jsonl` | 670 guard/control observations on 119 pairs. |
| `data/prevalence.jsonl`, `data/strata.json` | All 3,754 sampled pairs and stratum population sizes. |
| `data/codex.jsonl` | 712 scored Codex observations on 193 pairs. |
| `data/listing_probe.jsonl` | Default listing-budget measurements for 312 pairs on Opus 5. |
| `prompts.md` | Pair confirmation, core-function extraction, exclusive/shared labeling, and output-judging prompts. |
| `guard.py`, `skill_conflicts.json` | First-read hook and a two-skill configuration. |

## Fields

Rows join on `case_id`, `configuration`, and `model`. `replicate` identifies repeated runs; the main comparisons use the lowest completed replicate of each combination. Both `ok` and `timeout` runs are scored.

`A_only`, `B_only`, `both`, `both_swapped`, `scope_personal`, `scope_plugin`, and `A_plus_C` are the seven configurations. `both_guard` enables the guard. Model identifiers `sonnet`, `haiku`, and `opus` denote Claude Sonnet 4.6, Haiku 4.5, and Opus 5, run with Claude Code 2.1.283. Codex identifiers `gpt55`, `gpt56luna`, and `gpt56sol` denote `gpt-5.5`, `gpt-5.6-luna`, and `gpt-5.6-sol`.

Core-function verdicts are `1` (fulfilled), `0` (not fulfilled), or `null` (no scorable verdict). `top3_items` contains the primary core functions; `all_items` contains all applicable ones. `program_items` identifies verdicts settled by scripts. `top3_no_start_rate` omits checks already satisfied in the starting repository. `script_fidelity` is the program-check rate used for repeat agreement. Disclosure fields record whether the final reply mentions multiple skills, names the skill used, or asks the user to choose.

In `first_read.jsonl`, indices are zero-based tool-action positions; `delta_*` is the recorded change from the corresponding only-A run. Loss counts concern core functions fulfilled by only-A and lost after opening B first. In guard records, `first_skill` includes attempts denied by the hook; `used_A_after_first` records subsequent use of A. `null` denotes unavailable measurements. In prevalence records, `null` denotes unavailable type or installation-origin labels; `N` gives the population size of each stratum.

## Guard

Set `installed` and `similar` in `skill_conflicts.json` to the two skill directory names. Register `python3 /absolute/path/to/guard.py` as a Claude Code `PreToolUse` command hook. The hook reads the tool event from standard input, denies B while directing the model to A, and continues to deny B once A is in use. It writes hook decisions to `guard_log.jsonl` beside the script and keeps per-session state in the system temporary directory.
