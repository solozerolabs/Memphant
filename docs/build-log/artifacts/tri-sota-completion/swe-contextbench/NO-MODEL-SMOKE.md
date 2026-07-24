# SWE-ContextBench official-harness audit and no-model smoke

Status: official harness is public and importable; model execution is not authorized or run.

- Official code: `jiayuanz3/SWEContextBench` at `31bb04155f52b184bf31b220e3cff0607ac9c953`.
- Official dataset: `jiayuanz3/SWEContextBench` at `5bec275a2095768a53ac804ae4fdf90b1723b8af` (bare Git clone; LFS payloads were not downloaded).
- Code identity: `evaluation.sh` `4382682a3a387c930de0f106aeca48d6f04824a4bed37f4c92c9d9ece6ed26cd`; `combine_instances.py` `93f6c83c54979bfaa3d417e043c105a2b4e48a6192193b0db81882144e9a8557`; `run_evaluation.py` `e6b29452302df417cfde1493ab38817ac2d4ebb19750b374d60e383271ad5b69`.
- Official Lite cases present: 99.

No-model smoke commands:

```sh
python3 -m py_compile swebench_memory/harness/combine_instances.py \
  swebench_memory/harness/run_evaluation.py
PYTHONPATH=. python3 -m swebench_memory.harness.run_evaluation --help
PYTHONPATH=. python3 -m swebench_memory.harness.combine_instances \
  --instances 'cases/SWEContextBench Lite' \
  --predictions ../smoke/predictions \
  --dataset-output ../smoke/batch_dataset.json \
  --predictions-output ../smoke/batch_predictions.json
```

Result: exit 0; one empty-patch prediction matched the intended official case,
`matplotlib__matplotlib-24818`, at base commit
`e8101f17d8a7d2d7eccff7452162c02a27980800`. The combined dataset SHA-256 is
`03e827d923a4b2d5304e3e2f35d712661cc59e46c8617c6f445f2ffd8209d8be`; the
combined prediction SHA-256 is
`e187915925ccf33ca98b25f10977f89ec9e670ce67cc7e1c14886e33b0b79979`.

This proves only official input matching and evaluator importability. It does
not run Docker, generate a patch, execute task tests, measure MemPhant, or
establish any SWE-ContextBench score.
