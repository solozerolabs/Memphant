use std::fs;
use std::path::{Path, PathBuf};

use memphant_eval::{
    EvalRunOptions, generate_trace_schema, run_eval_file, run_ops_file, run_security_file,
    validate_manifest, verify_golden_file,
};

#[test]
fn oracle_suite_runs_and_verifies_load_bearing_labels() {
    let root = repo_root();
    let suite = root.join("examples/evals/golden.yaml");

    let report = run_eval_file(&suite, EvalRunOptions::default()).expect("golden run");
    assert_eq!(report.total_cases, 11);
    assert_eq!(
        report.passed_cases, report.total_cases,
        "{:#?}",
        report.case_results
    );
    assert!(report.case_results.iter().all(|case| case.passed));

    let verify = verify_golden_file(&suite).expect("verify golden");
    assert_eq!(verify.verified_cases, 11);
    assert!(verify.case_results.iter().all(|case| case.load_bearing));
}

#[test]
fn verify_golden_accepts_whole_corpus_directory() {
    let verify =
        verify_golden_file(&repo_root().join("examples/evals")).expect("verify golden directory");

    assert_eq!(verify.verified_cases, 11);
    assert!(verify.case_results.iter().all(|case| case.load_bearing));
}

#[test]
fn manifest_guard_rejects_orphans_and_missing_entries() {
    let temp = tempfile::tempdir().unwrap();
    let lane = temp.path().join("golden");
    fs::create_dir(&lane).unwrap();
    fs::write(lane.join("listed.yaml"), "id: listed\n").unwrap();
    fs::write(lane.join("orphan.yaml"), "id: orphan\n").unwrap();
    fs::write(
        temp.path().join("manifest.yaml"),
        "golden:\n  - listed\n  - missing\n",
    )
    .unwrap();

    let error = validate_manifest(&temp.path().join("manifest.yaml"), &lane)
        .expect_err("manifest should fail");
    let text = error.to_string();
    assert!(text.contains("orphan"));
    assert!(text.contains("missing"));
}

#[test]
fn security_smoke_covers_required_attack_lanes() {
    let report = run_security_file(&repo_root().join("examples/evals/security-smoke.yaml"))
        .expect("security smoke");

    assert!(report.passed);
    assert_eq!(
        report.covered_lanes,
        [
            "poisoning",
            "query_filter_injection",
            "high_risk_action_suppression",
            "tenant_leakage",
            "deletion_completeness",
        ]
    );
    assert!(
        report
            .lane_results
            .iter()
            .any(|lane| lane.kind == "deletion_completeness" && lane.passed)
    );
}

#[test]
fn security_smoke_refuses_crosscheck_lane_without_its_control() {
    // Non-vacuity for the `missing_no_crosscheck_control` guard (the rung6
    // `no-edges` idiom on the security path): strip the control lane and the
    // runner must refuse the paired cross-check poisoning lane.
    let text = fs::read_to_string(repo_root().join("examples/evals/security-smoke.yaml")).unwrap();
    let marker = "  - id: capture_poisoning_no_crosscheck_control";
    let cut = text
        .find(marker)
        .expect("control lane present in the committed fixture");
    let stripped = &text[..cut];
    assert!(
        stripped.contains("id: capture_poisoning_crosscheck"),
        "the cross-check promotion lane must survive the cut"
    );

    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("security-smoke-no-control.yaml");
    fs::write(&path, stripped).unwrap();

    let error = run_security_file(&path).expect_err("must refuse without the control");
    assert!(
        error.to_string().contains("missing_no_crosscheck_control"),
        "{error}"
    );
}

#[test]
fn ops_smoke_covers_blob_gc_deletion_saga_and_compaction() {
    let report = run_ops_file(&repo_root().join("examples/evals/ops-smoke.yaml")).expect("ops");

    assert!(report.passed);
    assert_eq!(
        report.covered_checks,
        [
            "blob_gc",
            "deletion_saga_readback",
            "reindex_compaction_sla",
        ]
    );
}

#[test]
fn benchmark_runner_archives_traces() {
    let temp = tempfile::tempdir().unwrap();
    let suite = repo_root().join("benchmarks/nightly-sampled.yaml");
    let report = run_eval_file(
        &suite,
        EvalRunOptions {
            archive_traces: true,
            archive_dir: Some(temp.path().to_path_buf()),
            ..EvalRunOptions::default()
        },
    )
    .expect("nightly sampled run");

    let archive = report.archived_trace_path.expect("archive path");
    assert!(archive.starts_with(temp.path()));
    let archive_json: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(archive).unwrap()).unwrap();
    assert_eq!(archive_json["eval_id"], "nightly-sampled");
    assert_eq!(archive_json["trace_schema_version"], "trace-0.1.0-ws0");
    assert!(!archive_json["case_results"].as_array().unwrap().is_empty());
}

#[test]
fn sampled_public_rung4_suite_proves_contextual_chunk_delta() {
    let suite = repo_root().join("benchmarks/rung4-lme-beam-sampled.yaml");
    let with_chunks = run_eval_file(&suite, EvalRunOptions::default()).expect("with chunks");
    assert_eq!(with_chunks.passed_cases, with_chunks.total_cases);

    let without_chunks = run_eval_file(
        &suite,
        EvalRunOptions {
            contextual_chunks_enabled: false,
            ..EvalRunOptions::default()
        },
    )
    .expect("without chunks");
    assert_eq!(without_chunks.total_cases, with_chunks.total_cases);
    assert_eq!(without_chunks.passed_cases, 0);
    assert!(
        without_chunks
            .case_results
            .iter()
            .all(|case| !case.missing_units.is_empty())
    );
}

#[test]
fn rung5_state_style_suite_proves_temporal_validity_delta() {
    let suite = repo_root().join("benchmarks/rung5-state-style-sampled.yaml");
    let with_temporal = run_eval_file(&suite, EvalRunOptions::default()).expect("with temporal");
    assert_eq!(with_temporal.passed_cases, with_temporal.total_cases);

    let without_temporal = run_eval_file(
        &suite,
        EvalRunOptions {
            temporal_validity_enabled: false,
            ..EvalRunOptions::default()
        },
    )
    .expect("without temporal");
    assert_eq!(without_temporal.total_cases, with_temporal.total_cases);
    assert_eq!(without_temporal.passed_cases, 0);
    assert!(
        without_temporal
            .case_results
            .iter()
            .all(|case| !case.forbidden_present.is_empty())
    );
}

#[test]
fn rung6_state_lme_suite_proves_edge_expansion_delta() {
    let suite = repo_root().join("benchmarks/rung6-state-lme-sampled.yaml");
    let with_edges = run_eval_file(&suite, EvalRunOptions::default()).expect("with edges");
    assert_eq!(with_edges.passed_cases, with_edges.total_cases);

    let without_edges = run_eval_file(
        &suite,
        EvalRunOptions {
            edge_expansion_enabled: false,
            ..EvalRunOptions::default()
        },
    )
    .expect("without edges");
    assert_eq!(without_edges.total_cases, with_edges.total_cases);
    assert_eq!(without_edges.passed_cases, 0);
    assert!(
        without_edges
            .case_results
            .iter()
            .all(|case| !case.missing_units.is_empty())
    );
}

#[test]
fn rung7_state_style_suite_proves_packing_abstention_delta() {
    let suite = repo_root().join("benchmarks/rung7-state-style-sampled.yaml");
    let with_packing = run_eval_file(&suite, EvalRunOptions::default()).expect("with packing");
    assert_eq!(with_packing.passed_cases, with_packing.total_cases);

    let without_packing = run_eval_file(
        &suite,
        EvalRunOptions {
            context_packing_abstention_enabled: false,
            ..EvalRunOptions::default()
        },
    )
    .expect("without packing");
    assert_eq!(without_packing.total_cases, with_packing.total_cases);
    assert_eq!(without_packing.passed_cases, 0);
    assert!(
        without_packing
            .case_results
            .iter()
            .all(|case| { !case.missing_units.is_empty() || !case.dropped_mismatches.is_empty() })
    );
}
#[test]
fn rung10_state_style_suite_proves_procedural_memory_delta() {
    let suite = repo_root().join("benchmarks/rung10-state-style-sampled.yaml");
    let with_procedure = run_eval_file(&suite, EvalRunOptions::default()).expect("with procedure");
    assert_eq!(with_procedure.passed_cases, with_procedure.total_cases);

    let without_procedure = run_eval_file(
        &suite,
        EvalRunOptions {
            procedure_recall_enabled: false,
            ..EvalRunOptions::default()
        },
    )
    .expect("without procedure");
    assert_eq!(without_procedure.total_cases, with_procedure.total_cases);
    assert_eq!(without_procedure.passed_cases, 0);
    assert!(
        without_procedure
            .case_results
            .iter()
            .all(|case| !case.missing_units.is_empty())
    );
}

#[test]
fn rung11_memorystress_style_suite_proves_dsr_decay_delta() {
    let suite = repo_root().join("benchmarks/rung11-memorystress-sampled.yaml");
    let with_decay = run_eval_file(&suite, EvalRunOptions::default()).expect("with decay");
    assert_eq!(
        with_decay.passed_cases, with_decay.total_cases,
        "{:#?}",
        with_decay.case_results
    );

    let without_decay = run_eval_file(
        &suite,
        EvalRunOptions {
            decay_enabled: false,
            ..EvalRunOptions::default()
        },
    )
    .expect("without decay");
    assert_eq!(without_decay.total_cases, with_decay.total_cases);
    assert_eq!(without_decay.passed_cases, 0);
    assert!(
        without_decay
            .case_results
            .iter()
            .all(|case| !case.forbidden_present.is_empty())
    );
}

#[test]
#[ignore = "paid network rung; requires configured MEMPHANT_DEEP=on environment"]
fn rung12_l4_exhaustive_suite_proves_raw_episode_delta() {
    let suite = repo_root().join("benchmarks/rung12-l4-exhaustive-sampled.yaml");
    let with_l4 = run_eval_file(
        &suite,
        EvalRunOptions {
            l4_runtime_provider: true,
            ..EvalRunOptions::default()
        },
    )
    .expect("with runtime Deep provider");
    assert_eq!(with_l4.passed_cases, with_l4.total_cases);

    let without_l4 = run_eval_file(
        &suite,
        EvalRunOptions {
            l4_exhaustive_enabled: false,
            ..EvalRunOptions::default()
        },
    )
    .expect("without l4 exhaustive");
    assert_eq!(without_l4.total_cases, with_l4.total_cases);
    assert_eq!(without_l4.passed_cases, 0);
    assert!(without_l4.case_results.iter().all(|case| {
        case.missing_units.is_empty()
            && case.trace_id.is_none()
            && case
                .error
                .as_deref()
                .is_some_and(|error| error.contains("deep recall is unavailable"))
    }));
}

#[test]
fn rung12_disabled_arm_is_explicitly_unavailable_not_fast_fallback() {
    let suite = repo_root().join("benchmarks/rung12-l4-exhaustive-sampled.yaml");
    let control = run_eval_file(
        &suite,
        EvalRunOptions {
            l4_exhaustive_enabled: false,
            ..EvalRunOptions::default()
        },
    )
    .expect("no-provider control completes as an eval result");

    assert_eq!(control.passed_cases, 0);
    let error = control.case_results[0]
        .error
        .as_deref()
        .expect("Deep no-provider arm records a typed error");
    assert!(error.contains("deep recall is unavailable"), "{error}");
    assert!(control.case_results[0].trace_id.is_none());
    assert!(control.case_results[0].missing_units.is_empty());
}

#[test]
fn rung12_fake_provider_arm_executes_while_control_is_unavailable() {
    let suite = repo_root().join("benchmarks/rung12-l4-exhaustive-sampled.yaml");
    let enabled = run_eval_file(&suite, EvalRunOptions::default()).unwrap();
    let control = run_eval_file(
        &suite,
        EvalRunOptions {
            l4_exhaustive_enabled: false,
            ..EvalRunOptions::default()
        },
    )
    .unwrap();

    assert!(enabled.case_results[0].error.is_none());
    assert!(enabled.case_results[0].trace_id.is_some());
    assert!(
        control.case_results[0]
            .error
            .as_deref()
            .is_some_and(|error| error.contains("deep recall is unavailable"))
    );
    assert!(control.case_results[0].trace_id.is_none());
}

#[test]
fn rung15_suite_proves_inferred_belief_composition_delta() {
    let suite = repo_root().join("benchmarks/rung15-inferred-belief-sampled.yaml");
    let with_composition =
        run_eval_file(&suite, EvalRunOptions::default()).expect("with inferred belief");
    assert_eq!(with_composition.passed_cases, with_composition.total_cases);

    let baseline = repo_root().join("benchmarks/rung15-baseline-sampled.yaml");
    let without_composition =
        run_eval_file(&baseline, EvalRunOptions::default()).expect("without inferred belief");
    assert_eq!(
        without_composition.total_cases,
        with_composition.total_cases
    );
    assert_eq!(without_composition.passed_cases, 0);
    assert!(
        without_composition
            .case_results
            .iter()
            .all(|case| !case.missing_units.is_empty())
    );
}

#[test]
fn trace_schema_snapshot_is_current() {
    let expected: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(repo_root().join("examples/evals/trace-schema.v1.json")).unwrap(),
    )
    .unwrap();
    assert_eq!(generate_trace_schema(), expected);
}

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .to_path_buf()
}
