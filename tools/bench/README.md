# tools/bench

Runs existing OAI timed tests several times on one Linux machine and summarises the timing they already print. It runs physims through ctest with their registered command line, and runs Google Benchmark binaries with `--benchmark_out`.

```bash
cmake -S . -B build -GNinja -DENABLE_TESTS=ON -DENABLE_PHYSIM_TESTS=ON
ninja -C build nr_dlsim nr_ulsim ldpctest benchmark_rotate_vector
python3 tools/bench/test_parser.py                            # parser self test
python3 tools/bench/run_bench.py --build-dir build --runs 10  # default test list
python3 tools/bench/run_bench.py --build-dir build --all      # every built nr_dlsim/nr_ulsim -P test
```

Results go to `results/<hostname>/`: `manifest.json` (CPU, kernel, compiler, build flags, git SHA, virtualization, load), raw `<test>/run<k>.log`, and `summary.{json,csv,md}`. The script waits until the 1 minute load average is below 1.0 (`--max-load`). Existing run logs are kept; use `--force` to rerun them. nr_dlsim and nr_ulsim print one timing block per SNR step: all blocks are in the JSON and CSV, and `summary.md` shows the first block only. 4G ulsim and nr_pucchsim are not included yet.
