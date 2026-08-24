# Layer 1 performance and acceptance

## Method

`benchmarks/layer1_benchmark.py` runs every measurement in an isolated subprocess. Each profile uses three uncached measurements, one preparation run, and five cache-hit measurements. Reports contain median wall time, maximum sampled RSS, cache latency, output size, a non-identifying source alias, and no absolute clinical-data path.

Profiles are generated or selected as follows:

- small: generated `64×64×32`, 12 inventory ROIs, 4 selected;
- representative Eclipse: PHPROLRT01 `329×205×525`, 164 CT slices;
- very large: generated `512×512×400`, 100 inventory ROIs, 16 selected.

The runner uses file-backed subprocess output capture so DICOM warning output cannot deadlock the monitor. `psutil` is used when installed; otherwise RSS is sampled with `ps`. A wall-time or RSS ratio above 1.10 is a regression unless explicitly justified.

## Recorded baselines

The small baseline on this host was:

| Mode | Median wall time | Peak RSS |
|---|---:|---:|
| Uncached, 3 runs | 0.671 s | 72,089,600 bytes |
| Cache hit, 5 runs | 0.479 s | 66,879,488 bytes |

The machine-readable small report is [benchmark_small.json](benchmark_small.json). Its baseline comparison also reports `performance_regression: false`.

The current representative report is machine-readable in [benchmark_representative_eclipse.json](benchmark_representative_eclipse.json). It is generated with:

| Mode | Median wall time | Peak RSS |
|---|---:|---:|
| Uncached, 3 runs | 13.086 s | 914,145,280 bytes |
| Cache hit, 5 runs | 1.402 s | 88,915,968 bytes |

Against the pre-upgrade representative baseline, wall time is 0.540× and peak RSS is 1.045×. Both remain inside the 10% regression gate; `performance_regression` is false. The source was staged locally before measurement so iCloud download latency was excluded.

```bash
python3 benchmarks/layer1_benchmark.py \
  --profile representative-eclipse \
  --source /path/to/PHPROLRT01-export \
  --config configs/ascend_case_config.example.json \
  --workspace /isolated/scratch/representative \
  --output docs/benchmark_representative_eclipse.json \
  --baseline /path/to/previous/representative.json
```

The generated very-large report is [benchmark_very_large.json](benchmark_very_large.json):

| Mode | Median wall time | Peak RSS |
|---|---:|---:|
| Uncached, 3 runs | 72.989 s | 2,055,012,352 bytes |
| Cache hit, 5 runs | 2.946 s | 79,265,792 bytes |

It deliberately exercises multi-gigabyte dose decoding and publication. Generate it on deployment hardware with:

```bash
python3 benchmarks/generate_eclipse_fixture.py /isolated/scratch/very-large/source --profile very-large
python3 benchmarks/layer1_benchmark.py \
  --profile very-large \
  --source /isolated/scratch/very-large/source \
  --config /isolated/scratch/very-large/source/benchmark_config.json \
  --workspace /isolated/scratch/very-large/runs \
  --output /isolated/scratch/very-large/result.json
```

An optimization is accepted only when its target improves reproducibly, the 10% regression gate is satisfied or explicitly justified, cache and uncached artifacts remain equivalent, and locked source hashes remain unchanged.
