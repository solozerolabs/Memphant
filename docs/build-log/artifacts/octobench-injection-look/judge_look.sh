#!/bin/bash
cd "$(dirname "$0")"
V=.venv/bin/python
for arm in baseline inject; do
  echo "=== arm: $arm ==="
  if [ ! -s results/merged_$arm.jsonl ]; then
    echo "converting $arm..."
    $V convert/convert_cc_traj_to_msg.py --input_path results/$arm --output_path results/merged_$arm.jsonl 2>&1 | tail -2
  else
    echo "merged_$arm.jsonl exists, skip convert"
  fi
  echo "judging $arm..."
  doppler run --project syndai --config dev -- $V evaluate.py \
    --trajectories results/merged_$arm.jsonl --data _look_subset.jsonl \
    --output results/scores_$arm.json --model gpt-4o 2>&1 | tail -4
done
echo "JUDGE_EXIT rc=$?"
