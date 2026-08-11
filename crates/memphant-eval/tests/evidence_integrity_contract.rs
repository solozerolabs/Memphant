//! spec 31 evidence-integrity gate. The suite's cases assert MemPhant's
//! trace-integrity machinery (valid-time filtering, as-of recall, contradiction
//! → abstention, citation attribution, answer grounding). Each case's `expect`
//! IS the metric gate: a regression in the targeted machinery flips the case to
//! failing (verified by perturbation during authoring — remove the contradicts
//! edge and the conflict cases stop abstaining; swap the query and the
//! adversarial cases stop citing the correct unit). This test is the standalone
//! runner for that suite, kept out of the pr-golden profile per spec 31 §3.4.

use std::path::{Path, PathBuf};

use memphant_eval::{EvalRunOptions, run_eval_file, verify_golden_file};

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .to_path_buf()
}

fn suite() -> PathBuf {
    repo_root().join("benchmarks/evidence-integrity-sampled.yaml")
}

/// Every metric-bearing regime must be present AND pass. The explicit id list
/// guards against the suite being silently emptied or a regime being dropped —
/// a green "0/0" run must never masquerade as a passing integrity gate.
const REQUIRED_CASE_IDS: [&str; 10] = [
    // stale (valid-time) + as-of (valid_at) — as_of_correctness
    "evidence_integrity_stale_office",
    "evidence_integrity_asof_concurrency",
    "evidence_integrity_stale_flag_supersede",
    // conflict (contradicts → abstain) — conflict_evidence_recall
    "evidence_integrity_conflict_owner",
    "evidence_integrity_conflict_endpoint",
    // adversarial (dedup arbitration) — citation_justification
    "evidence_integrity_adversarial_region",
    "evidence_integrity_adversarial_version",
    // grounding — unsupported_answer_rate
    "evidence_integrity_ground_value",
    // suppression precedes access/recency accounting — suppressed_read_no_refresh
    "evidence_integrity_suppressed_read_no_refresh_superseded",
    "evidence_integrity_suppressed_read_no_refresh_contradiction",
];

#[test]
fn evidence_integrity_suite_passes_all_metrics() {
    let report = run_eval_file(&suite(), EvalRunOptions::default()).expect("run suite");

    assert_eq!(
        report.total_cases,
        REQUIRED_CASE_IDS.len(),
        "suite size drifted from the metric-bearing set"
    );
    assert_eq!(
        report.passed_cases,
        report.total_cases,
        "evidence-integrity regressions: {:#?}",
        report
            .case_results
            .iter()
            .filter(|case| !case.passed)
            .collect::<Vec<_>>()
    );

    let present: std::collections::BTreeSet<&str> =
        report.case_results.iter().map(|c| c.id.as_str()).collect();
    for id in REQUIRED_CASE_IDS {
        assert!(
            present.contains(id),
            "required evidence-integrity case {id} is missing from the suite"
        );
    }
}

#[test]
fn evidence_integrity_cases_are_load_bearing() {
    let verify = verify_golden_file(&suite()).expect("verify suite");
    assert_eq!(verify.verified_cases, REQUIRED_CASE_IDS.len());
    assert!(
        verify.case_results.iter().all(|case| case.load_bearing),
        "a case passes even with its evidence masked — it asserts nothing real: {:#?}",
        verify
            .case_results
            .iter()
            .filter(|case| !case.load_bearing)
            .collect::<Vec<_>>()
    );
}
