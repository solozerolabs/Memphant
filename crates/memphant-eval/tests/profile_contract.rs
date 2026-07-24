use std::fs;
use std::path::Path;

use memphant_eval::run_profile_file;

fn repo_root() -> &'static Path {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .expect("workspace root")
}

#[test]
fn wsi_profile_archives_dormant_advanced_lever_decisions() {
    let archive_dir = tempfile::tempdir().expect("tempdir");
    let archive_path = archive_dir.path().join("wsi-profile.json");
    let report = run_profile_file(
        &repo_root().join("examples/evals/wsi-profile.yaml"),
        "rungs-0-3-baseline",
        Some(archive_path.clone()),
    )
    .expect("profile should pass");

    assert!(report.activated_levers.is_empty());
    assert!(
        report
            .dormant_levers
            .iter()
            .any(|item| item == "L4 exhaustive recall behavior")
    );
    assert_eq!(report.archived_path.as_ref(), Some(&archive_path));
    assert!(archive_path.is_file());
    let archived: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&archive_path).expect("read archive"))
            .expect("archive json");
    assert_eq!(
        archived["archived_path"],
        archive_path.display().to_string()
    );
}

#[test]
fn activated_profile_decision_requires_after_trace_evidence() {
    let source = fs::read_to_string(repo_root().join("examples/evals/wsi-profile.yaml"))
        .expect("read fixture");
    let bad = source.replacen(
        "status: dormant\n    gate_met: false",
        "status: activated\n    gate_met: true",
        1,
    );
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("bad-profile.yaml");
    fs::write(&path, bad).expect("write fixture");

    let error = run_profile_file(&path, "rungs-0-3-baseline", None)
        .expect_err("activated lever without after trace should fail");

    assert!(error.to_string().contains("missing_after_trace"));
}

#[test]
fn rung4_profile_archives_contextual_chunk_promotion_with_public_sampled_axes() {
    let archive_dir = tempfile::tempdir().expect("tempdir");
    let archive_path = archive_dir.path().join("rung4-profile.json");
    let report = run_profile_file(
        &repo_root().join("examples/evals/rung4-contextual-chunks-profile.yaml"),
        "rungs-0-3-baseline",
        Some(archive_path.clone()),
    )
    .expect("rung4 profile should pass");

    let decision = report
        .rung_decisions
        .iter()
        .find(|decision| decision.rung == 4)
        .expect("rung 4 decision");
    assert_eq!(decision.item, "contextual chunks");
    assert_eq!(decision.status, "promoted");
    assert!(decision.gate_met);
    assert_eq!(decision.axes, ["long_horizon", "scale"]);
    assert!(decision.delta_vs_baseline > 0.0);
    assert!(decision.ci[0] > 0.0);
    assert!(decision.p95_ms > 0.0);
    assert!(decision.cost_per_1k_recalls_usd >= 0.0);

    let archived: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&archive_path).expect("read archive"))
            .expect("archive json");
    assert_eq!(archived["rung_decisions"][0]["rung"], 4);
    assert_eq!(
        archived["rung_decisions"][0]["benchmark_sample_refs"][0],
        "hf:xiaowu0162/longmemeval-v2@2026-05-17/questions.jsonl"
    );
}

#[test]
fn rung4_promotion_requires_lme_and_beam_axes() {
    let source =
        fs::read_to_string(repo_root().join("examples/evals/rung4-contextual-chunks-profile.yaml"))
            .expect("read fixture");
    let bad = source.replace("axes: [long_horizon, scale]", "axes: [long_horizon]");
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("bad-rung4-profile.yaml");
    fs::write(&path, bad).expect("write fixture");

    let error = run_profile_file(&path, "rungs-0-3-baseline", None)
        .expect_err("rung4 promotion without BEAM axis should fail");

    assert!(
        error
            .to_string()
            .contains("rung_decision:4:missing_scale_axis")
    );
}

#[test]
fn rung5_profile_archives_temporal_validity_promotion() {
    let archive_dir = tempfile::tempdir().expect("tempdir");
    let archive_path = archive_dir.path().join("rung5-profile.json");
    let report = run_profile_file(
        &repo_root().join("examples/evals/rung5-temporal-validity-profile.yaml"),
        "rungs-0-4-baseline",
        Some(archive_path.clone()),
    )
    .expect("rung5 profile should pass");

    let decision = report
        .rung_decisions
        .iter()
        .find(|decision| decision.rung == 5)
        .expect("rung 5 decision");
    assert_eq!(decision.item, "temporal validity");
    assert_eq!(decision.status, "promoted");
    assert_eq!(decision.axes, ["outcome", "interactive"]);
    assert!(decision.delta_vs_baseline > 0.0);
    assert!(decision.ci[0] > 0.0);

    let archived: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&archive_path).expect("read archive"))
            .expect("archive json");
    assert_eq!(archived["rung_decisions"][0]["rung"], 5);
}

#[test]
fn rung5_promotion_requires_state_style_axis() {
    let source =
        fs::read_to_string(repo_root().join("examples/evals/rung5-temporal-validity-profile.yaml"))
            .expect("read fixture");
    let bad = source.replace("axes: [outcome, interactive]", "axes: [outcome]");
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("bad-rung5-profile.yaml");
    fs::write(&path, bad).expect("write fixture");

    let error = run_profile_file(&path, "rungs-0-4-baseline", None)
        .expect_err("rung5 promotion without STATE-style axis should fail");

    assert!(
        error
            .to_string()
            .contains("rung_decision:5:missing_interactive_axis")
    );
}

#[test]
fn rung6_profile_archives_edge_expansion_promotion_with_controls() {
    let archive_dir = tempfile::tempdir().expect("tempdir");
    let archive_path = archive_dir.path().join("rung6-profile.json");
    let report = run_profile_file(
        &repo_root().join("examples/evals/rung6-edge-expansion-profile.yaml"),
        "rungs-0-5-baseline",
        Some(archive_path.clone()),
    )
    .expect("rung6 profile should pass");

    let decision = report
        .rung_decisions
        .iter()
        .find(|decision| decision.rung == 6)
        .expect("rung 6 decision");
    assert_eq!(decision.item, "edge expansion");
    assert_eq!(decision.status, "promoted");
    assert_eq!(decision.axes, ["outcome", "long_horizon", "interactive"]);
    assert!(decision.delta_vs_baseline >= 0.03);
    assert!(decision.ci[0] > 0.0);
    assert!(
        decision
            .benchmark_sample_refs
            .iter()
            .any(|sample| sample.contains("no-edges"))
    );
    assert!(
        decision
            .benchmark_sample_refs
            .iter()
            .any(|sample| sample.contains("filesystem-control"))
    );

    let archived: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&archive_path).expect("read archive"))
            .expect("archive json");
    assert_eq!(archived["rung_decisions"][0]["rung"], 6);
}

#[test]
fn rung6_promotion_requires_no_edges_and_filesystem_controls() {
    let source =
        fs::read_to_string(repo_root().join("examples/evals/rung6-edge-expansion-profile.yaml"))
            .expect("read fixture");
    let bad = source
        .replace(
            "      - no-edges:benchmarks/rung6-no-edges-sampled.yaml\n",
            "",
        )
        .replace(
            "      - filesystem-control:benchmarks/rung6-filesystem-control-sampled.yaml\n",
            "",
        );
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("bad-rung6-profile.yaml");
    fs::write(&path, bad).expect("write fixture");

    let error = run_profile_file(&path, "rungs-0-5-baseline", None)
        .expect_err("rung6 promotion without controls should fail");

    let text = error.to_string();
    assert!(text.contains("rung_decision:6:missing_no_edges_control"));
    assert!(text.contains("rung_decision:6:missing_filesystem_control"));
}

#[test]
fn rung7_profile_archives_packing_abstention_promotion() {
    let archive_dir = tempfile::tempdir().expect("tempdir");
    let archive_path = archive_dir.path().join("rung7-profile.json");
    let report = run_profile_file(
        &repo_root().join("examples/evals/rung7-packing-abstention-profile.yaml"),
        "rungs-0-6-baseline",
        Some(archive_path.clone()),
    )
    .expect("rung7 profile should pass");

    let decision = report
        .rung_decisions
        .iter()
        .find(|decision| decision.rung == 7)
        .expect("rung 7 decision");
    assert_eq!(decision.item, "packing+abstention");
    assert_eq!(decision.status, "promoted");
    assert_eq!(decision.axes, ["outcome", "restraint"]);
    assert!(decision.delta_vs_baseline > 0.0);
    assert!(decision.ci[0] > 0.0);
    assert!(
        decision
            .benchmark_sample_refs
            .iter()
            .any(|sample| sample.contains("packing_abstention_buried_deploy"))
    );
    assert!(
        decision
            .benchmark_sample_refs
            .iter()
            .any(|sample| sample.contains("packing_abstention_contradiction"))
    );

    let archived: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&archive_path).expect("read archive"))
            .expect("archive json");
    assert_eq!(archived["rung_decisions"][0]["rung"], 7);
}

#[test]
fn rung7_promotion_requires_packing_and_abstention_samples() {
    let source = fs::read_to_string(
        repo_root().join("examples/evals/rung7-packing-abstention-profile.yaml"),
    )
    .expect("read fixture");
    let bad = source
        .replace(
            "      - memphant:examples/evals/golden/packing_abstention_buried_deploy.yaml\n",
            "",
        )
        .replace(
            "      - memphant:examples/evals/golden/packing_abstention_contradiction.yaml\n",
            "",
        );
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("bad-rung7-profile.yaml");
    fs::write(&path, bad).expect("write fixture");

    let error = run_profile_file(&path, "rungs-0-6-baseline", None)
        .expect_err("rung7 promotion without samples should fail");

    let text = error.to_string();
    assert!(text.contains("rung_decision:7:missing_packing_sample"));
    assert!(text.contains("rung_decision:7:missing_abstention_sample"));
}

#[test]
fn rung10_profile_archives_procedural_memory_promotion() {
    let archive_dir = tempfile::tempdir().expect("tempdir");
    let archive_path = archive_dir.path().join("rung10-profile.json");
    let report = run_profile_file(
        &repo_root().join("examples/evals/rung10-procedural-memory-profile.yaml"),
        "rungs-0-9-baseline",
        Some(archive_path.clone()),
    )
    .expect("rung10 profile should pass");

    let decision = report
        .rung_decisions
        .iter()
        .find(|decision| decision.rung == 10)
        .expect("rung 10 decision");
    assert_eq!(decision.item, "procedural memory");
    assert_eq!(decision.status, "promoted");
    assert_eq!(decision.axes, ["outcome", "procedural", "interactive"]);
    assert!(decision.delta_vs_baseline > 0.0);
    assert!(decision.ci[0] > 0.0);
    assert!(
        decision
            .benchmark_sample_refs
            .iter()
            .any(|sample| sample.contains("procedural_memory_replay_validation"))
    );
    assert!(
        decision
            .benchmark_sample_refs
            .iter()
            .any(|sample| sample.contains("no-procedure"))
    );
    assert!(
        report
            .activated_levers
            .iter()
            .any(|item| item == "Procedural replay-validation harness")
    );

    let archived: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&archive_path).expect("read archive"))
            .expect("archive json");
    assert_eq!(archived["rung_decisions"][0]["rung"], 10);
}

#[test]
fn rung10_promotion_requires_replay_sample_and_no_procedure_control() {
    let source = fs::read_to_string(
        repo_root().join("examples/evals/rung10-procedural-memory-profile.yaml"),
    )
    .expect("read fixture");
    let bad = source
        .replace(
            "      - memphant:examples/evals/golden/procedural_memory_replay_validation.yaml\n",
            "",
        )
        .replace(
            "      - no-procedure:benchmarks/rung10-baseline-sampled.yaml\n",
            "",
        );
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("bad-rung10-profile.yaml");
    fs::write(&path, bad).expect("write fixture");

    let error = run_profile_file(&path, "rungs-0-9-baseline", None)
        .expect_err("rung10 promotion without sample/control should fail");

    let text = error.to_string();
    assert!(text.contains("rung_decision:10:missing_procedural_replay_sample"));
    assert!(text.contains("rung_decision:10:missing_no_procedure_control"));
}

#[test]
fn rung11_profile_archives_dsr_decay_promotion() {
    let archive_dir = tempfile::tempdir().expect("tempdir");
    let archive_path = archive_dir.path().join("rung11-profile.json");
    let report = run_profile_file(
        &repo_root().join("examples/evals/rung11-dsr-decay-profile.yaml"),
        "rungs-0-10-baseline",
        Some(archive_path.clone()),
    )
    .expect("rung11 profile should pass");

    let decision = report
        .rung_decisions
        .iter()
        .find(|decision| decision.rung == 11)
        .expect("rung 11 decision");
    assert_eq!(decision.item, "DSR decay");
    assert_eq!(decision.status, "promoted");
    assert_eq!(decision.axes, ["longitudinal", "interactive"]);
    assert!(decision.delta_vs_baseline > 0.0);
    assert!(decision.ci[0] > 0.0);
    assert!(
        decision
            .benchmark_sample_refs
            .iter()
            .any(|sample| sample.contains("rung11-memorystress"))
    );
    assert!(
        decision
            .benchmark_sample_refs
            .iter()
            .any(|sample| sample.contains("no-decay"))
    );
    assert!(
        report
            .activated_levers
            .iter()
            .any(|item| item == "DSR decay fold")
    );

    let archived: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&archive_path).expect("read archive"))
            .expect("archive json");
    assert_eq!(archived["rung_decisions"][0]["rung"], 11);
}

#[test]
fn rung11_promotion_requires_memorystress_and_no_decay_control() {
    let source =
        fs::read_to_string(repo_root().join("examples/evals/rung11-dsr-decay-profile.yaml"))
            .expect("read fixture");
    let bad = source
        .replace(
            "      - memorystress-style:benchmarks/rung11-memorystress-sampled.yaml\n",
            "",
        )
        .replace(
            "      - no-decay:benchmarks/rung11-baseline-sampled.yaml\n",
            "",
        );
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("bad-rung11-profile.yaml");
    fs::write(&path, bad).expect("write fixture");

    let error = run_profile_file(&path, "rungs-0-10-baseline", None)
        .expect_err("rung11 promotion without MemoryStress/no-decay proof should fail");

    let text = error.to_string();
    assert!(text.contains("rung_decision:11:missing_memorystress_sample"));
    assert!(text.contains("rung_decision:11:missing_no_decay_control"));
}

#[test]
fn rung12_profile_archives_l4_exhaustive_promotion() {
    let archive_dir = tempfile::tempdir().expect("tempdir");
    let archive_path = archive_dir.path().join("rung12-profile.json");
    let report = run_profile_file(
        &repo_root().join("examples/evals/rung12-l4-exhaustive-profile.yaml"),
        "rungs-0-11-baseline",
        Some(archive_path.clone()),
    )
    .expect("rung12 profile should pass");

    let decision = report
        .rung_decisions
        .iter()
        .find(|decision| decision.rung == 12)
        .expect("rung 12 decision");
    assert_eq!(decision.item, "L4 exhaustive recall");
    assert_eq!(decision.status, "promoted");
    assert_eq!(decision.axes, ["long_horizon", "scale", "interactive"]);
    assert!(decision.delta_vs_baseline > 0.0);
    assert!(decision.ci[0] > 0.0);
    assert!(
        decision
            .benchmark_sample_refs
            .iter()
            .any(|sample| sample.contains("l4_exhaustive_raw_episode_buried"))
    );
    assert!(
        decision
            .benchmark_sample_refs
            .iter()
            .any(|sample| sample.contains("no-l4"))
    );
    assert!(
        report
            .activated_levers
            .iter()
            .any(|item| item == "L4 exhaustive recall behavior")
    );

    let archived: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&archive_path).expect("read archive"))
            .expect("archive json");
    assert_eq!(archived["rung_decisions"][0]["rung"], 12);
}

#[test]
fn current_rung12_profile_accepts_deep_names() {
    let source =
        fs::read_to_string(repo_root().join("examples/evals/rung12-l4-exhaustive-profile.yaml"))
            .expect("read legacy fixture");
    let current = source
        .replace(
            "benchmark_version: l4-exhaustive-raw-episode-2026-07-03",
            "benchmark_version: agentic-deep-raw-source-2026-07-20",
        )
        .replace("item: L4 exhaustive recall", "item: L4 Deep recall");
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("current-rung12-profile.yaml");
    fs::write(&path, current).expect("write fixture");

    let report = run_profile_file(&path, "rungs-0-11-baseline", None)
        .expect("current Deep profile should pass");

    assert_eq!(report.rung_decisions[0].item, "L4 Deep recall");
    assert!(
        report
            .activated_levers
            .iter()
            .any(|item| item == "L4 Deep recall behavior")
    );
}

#[test]
fn current_rung12_profile_rejects_retired_public_names() {
    let source =
        fs::read_to_string(repo_root().join("examples/evals/rung12-l4-exhaustive-profile.yaml"))
            .expect("read legacy fixture");
    let current = source.replace(
        "benchmark_version: l4-exhaustive-raw-episode-2026-07-03",
        "benchmark_version: agentic-deep-raw-source-2026-07-20",
    );
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("retired-rung12-profile.yaml");
    fs::write(&path, current).expect("write fixture");

    let error = run_profile_file(&path, "rungs-0-11-baseline", None)
        .expect_err("current profiles must use Deep names");
    let text = error.to_string();

    assert!(text.contains("activation_decision:missing:L4 Deep recall behavior"));
    assert!(text.contains("rung_decision:12:invalid_item:L4 exhaustive recall"));
}

#[test]
fn rung12_promotion_requires_l4_sample_and_no_l4_control() {
    let source =
        fs::read_to_string(repo_root().join("examples/evals/rung12-l4-exhaustive-profile.yaml"))
            .expect("read fixture");
    let bad = source
        .replace(
            "      - memphant:examples/evals/golden/l4_exhaustive_raw_episode_buried.yaml\n",
            "",
        )
        .replace(
            "      - no-l4:benchmarks/rung12-baseline-sampled.yaml\n",
            "",
        );
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("bad-rung12-profile.yaml");
    fs::write(&path, bad).expect("write fixture");

    let error = run_profile_file(&path, "rungs-0-11-baseline", None)
        .expect_err("rung12 promotion without L4 sample/control should fail");

    let text = error.to_string();
    assert!(text.contains("rung_decision:12:missing_l4_exhaustive_sample"));
    assert!(text.contains("rung_decision:12:missing_no_l4_control"));
}

#[test]
fn rung14_profile_archives_external_engine_retirement() {
    let archive_dir = tempfile::tempdir().expect("tempdir");
    let archive_path = archive_dir.path().join("rung14-profile.json");
    let report = run_profile_file(
        &repo_root().join("examples/evals/rung14-external-engine-retirement-profile.yaml"),
        "rungs-0-13-baseline",
        Some(archive_path.clone()),
    )
    .expect("rung14 profile should pass");

    let decision = report
        .rung_decisions
        .iter()
        .find(|decision| decision.rung == 14)
        .expect("rung 14 decision");
    assert_eq!(decision.item, "external graph/vector escape hatch");
    assert_eq!(decision.status, "retired");
    assert!(!decision.gate_met);
    assert_eq!(
        decision.axes,
        ["outcome", "long_horizon", "scale", "systems_cost"]
    );
    assert_eq!(decision.delta_vs_baseline, 0.0);
    assert_eq!(decision.ci, [0.0, 0.0]);
    assert!(
        decision
            .benchmark_sample_refs
            .iter()
            .any(|sample| sample.contains("edge_expansion_runbook_lineage"))
    );
    assert!(
        decision
            .benchmark_sample_refs
            .iter()
            .any(|sample| sample.contains("no-edges"))
    );
    assert!(
        decision
            .benchmark_sample_refs
            .iter()
            .any(|sample| sample.contains("pgvector-default:wsi-local-sota-profile"))
    );
    assert!(
        report
            .retired_levers
            .iter()
            .any(|item| item == "External graph DB / dedicated vector engine")
    );

    let archived: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&archive_path).expect("read archive"))
            .expect("archive json");
    assert_eq!(archived["rung_decisions"][0]["rung"], 14);
}

#[test]
fn rung14_retirement_requires_relational_edge_control_and_pgvector_evidence() {
    let source = fs::read_to_string(
        repo_root().join("examples/evals/rung14-external-engine-retirement-profile.yaml"),
    )
    .expect("read fixture");
    let bad = source
        .replace(
            "      - relational-edge:examples/evals/golden/edge_expansion_runbook_lineage.yaml\n",
            "",
        )
        .replace(
            "      - no-edges:benchmarks/rung6-no-edges-sampled.yaml\n",
            "",
        )
        .replace("      - pgvector-default:wsi-local-sota-profile\n", "");
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("bad-rung14-profile.yaml");
    fs::write(&path, bad).expect("write fixture");

    let error = run_profile_file(&path, "rungs-0-13-baseline", None)
        .expect_err("rung14 retirement without relational/pgvector proof should fail");

    let text = error.to_string();
    assert!(text.contains("rung_decision:14:missing_relational_edge_sample"));
    assert!(text.contains("rung_decision:14:missing_no_edges_control"));
    assert!(text.contains("rung_decision:14:missing_pgvector_profile_ref"));
}

#[test]
fn rung15_profile_archives_inferred_belief_composition_promotion() {
    let archive_dir = tempfile::tempdir().expect("tempdir");
    let archive_path = archive_dir.path().join("rung15-profile.json");
    let report = run_profile_file(
        &repo_root().join("examples/evals/rung15-inferred-belief-composition-profile.yaml"),
        "rungs-0-14-baseline",
        Some(archive_path.clone()),
    )
    .expect("rung15 profile should pass");

    let decision = report
        .rung_decisions
        .iter()
        .find(|decision| decision.rung == 15)
        .expect("rung 15 decision");
    assert_eq!(decision.item, "inferred-belief composition");
    assert_eq!(decision.status, "promoted");
    assert!(decision.gate_met);
    assert_eq!(decision.axes, ["outcome", "interactive", "restraint"]);
    assert!(decision.delta_vs_baseline >= 0.03);
    assert!(decision.ci[0] >= 0.03);
    assert!(
        decision
            .benchmark_sample_refs
            .iter()
            .any(|sample| sample.contains("inferred_belief_composition"))
    );
    assert!(
        decision
            .benchmark_sample_refs
            .iter()
            .any(|sample| sample.contains("no-composition"))
    );
    assert!(
        decision
            .benchmark_sample_refs
            .iter()
            .any(|sample| sample.contains("op-bench"))
    );

    let archived: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&archive_path).expect("read archive"))
            .expect("archive json");
    assert_eq!(archived["rung_decisions"][0]["rung"], 15);
}

#[test]
fn rung15_promotion_requires_sample_control_and_restraint_reference() {
    let source = fs::read_to_string(
        repo_root().join("examples/evals/rung15-inferred-belief-composition-profile.yaml"),
    )
    .expect("read fixture");
    let bad = source
        .replace(
            "      - memphant:examples/evals/golden/inferred_belief_composition.yaml\n",
            "",
        )
        .replace(
            "      - no-composition:benchmarks/rung15-baseline-sampled.yaml\n",
            "",
        )
        .replace(
            "      - op-bench-style:no-regression:docs/build-log/artifacts/rung15-inferred-belief-sampled-traces.json\n",
            "",
        );
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("bad-rung15-profile.yaml");
    fs::write(&path, bad).expect("write fixture");

    let error = run_profile_file(&path, "rungs-0-14-baseline", None)
        .expect_err("rung15 promotion without sample/control/restraint proof should fail");

    let text = error.to_string();
    assert!(text.contains("rung_decision:15:missing_inferred_belief_sample"));
    assert!(text.contains("rung_decision:15:missing_no_composition_control"));
    assert!(text.contains("rung_decision:15:missing_op_bench_restraint_ref"));
}
