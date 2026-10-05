# Memory Profiling (memray)

How to find what holds memory in a running MLPA pod. Built for [AIPLAT-1392](https://mozilla-hub.atlassian.net/browse/AIPLAT-1392) (pods climb to 100% memory and get OOM-killed).

We use [memray](https://github.com/bloomberg/memray). It records every allocation, including native (C) ones, so it also sees leaks that Python-only tools miss.

## Enable

`docker-entrypoint.sh` runs MLPA under memray when `MEMRAY_ENABLED=true`. It is off by default.

| Env var | Default | Meaning |
|---|---|---|
| `MEMRAY_ENABLED` | `false` | Run MLPA under `memray run --native` |
| `MEMRAY_OUTPUT` | `/tmp/memray/mlpa.bin` | Where the capture file is written |

Use it on one stage pod only. memray adds CPU and memory overhead.

## Disk usage

The file records every allocation and free, so it grows with allocation volume, not with leak size. It has no size limit.

1. Check the size 15 and 30 min after enabling: `kubectl -n llm-proxy-stage exec <pod> -c <mlpa-container> -- ls -la /tmp/memray/`
2. Extrapolate to 48h. If it gets too big, turn the profiler off (unset `MEMRAY_ENABLED`).

A container restart (for example an OOM kill) wipes `/tmp`. Copy the file out before the pod gets close to 100% memory.

## Get the file

```
kubectl -n llm-proxy-stage cp -c <mlpa-container> <pod>:/tmp/memray/mlpa.bin ./mlpa.bin
```

Copying a file that is still being written works. For big files, gzip it in the pod first:

```
kubectl -n llm-proxy-stage exec <pod> -c <mlpa-container> -- gzip -c /tmp/memray/mlpa.bin > mlpa.bin.gz
```

## Analyze

Use the same memray version as the image (see `pyproject.toml`):

```
uvx --from memray==1.20.0 memray flamegraph --leaks mlpa.bin -o leaks.html
open leaks.html
```

`--leaks` shows only allocations that were never freed when the file was cut. Wide bars are the call stacks holding the most memory.

Other views:
- `memray table --leaks mlpa.bin`: same data as a sortable table
- `memray stats mlpa.bin`: totals and peak

## Read the result

1. Copy the file at least twice (for example at ~6h and ~24h).
2. Compare the top stacks. A real leak grows between the two. Startup costs (imports, schema builds, trust-store loads) stay flat.
3. Native frames show up because of `--native`. If the file seems wrong or the pod fails to start, remove `--native` from the entrypoint first.

## Turn off

Unset `MEMRAY_ENABLED` (or revert the manual `kubectl set env`) and the pod restarts without the profiler. If Argo CD sync was paused for a manual change, re-enable it so the live state matches git.
