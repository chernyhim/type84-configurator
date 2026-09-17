# Configuration Experiment Batch Analysis Report

> **Confidence Rule:** A single experiment yields at most `PROBABLE` status. Status `CONFIRMED` is assigned only with $\ge 2$ independent repeatable confirmations.

## 1. Summary Correlation Table

| Key | Slot | Bank:Column | Offset | Address | Parameter | Confidence | Experiments |
| :--- | :---: | :---: | :---: | :---: | :--- | :---: | :--- |
| **A** | `S049` | B3:C01 | `+5` | `0x018D` | Actuation Point | `PROBABLE` | exp_key_a_actuation |
| **S** | `S050` | B3:C02 | `+5` | `0x0195` | Actuation Point | `PROBABLE` | exp_key_s_actuation |
| **D** | `S051` | B3:C03 | `+5` | `0x019D` | Actuation Point | `PROBABLE` | exp_key_d_actuation |

## 2. 8-Byte Slot Structure

| Offset | Parameter | Tested Keys | Status |
| :---: | :--- | :--- | :---: |
| `+0` | *Unknown* | - | `UNKNOWN` |
| `+1` | *Unknown* | - | `UNKNOWN` |
| `+2` | *Unknown* | - | `UNKNOWN` |
| `+3` | *Unknown* | - | `UNKNOWN` |
| `+4` | *Unknown* | - | `UNKNOWN` |
| `+5` | **Actuation Point** | A, D, S | `CONFIRMED` |
| `+6` | *Unknown* | - | `UNKNOWN` |
| `+7` | *Unknown* | - | `UNKNOWN` |