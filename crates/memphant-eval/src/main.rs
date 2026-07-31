use std::path::PathBuf;
use std::process::ExitCode;

use memphant_eval::{
    EvalReport, EvalRunOptions, generate_trace_schema, run_eval_file, run_ops_file,
    run_profile_file, run_security_file, run_syndai_trace_compare_file, verify_golden_file,
};

mod pool_tools;

/// Print the Deep settlement receipt for every case that ran a Deep provider
/// (P0.3 §6: surface the settle-on-abort receipt to the operator regardless of
/// pass/fail). No-op when no case ran Deep.
fn print_deep_settlements(report: &EvalReport) {
    for case in &report.case_results {
        if let Some(deep) = &case.deep {
            println!(
                "deep_settlement case={} status={:?} stop_reason={:?} settled_micros={} unsettled_upper_bound_micros={} tool_iterations={} wall_time_ms={} gathered_evidence_ids={:?} generation_ids={:?}",
                case.id,
                deep.status,
                deep.stop_reason,
                deep.settled_micros,
                deep.unsettled_micros_upper_bound,
                deep.tool_iterations,
                deep.wall_time_ms,
                deep.gathered_evidence_ids,
                deep.generation_ids,
            );
        }
    }
}

fn main() -> ExitCode {
    let mut args = std::env::args().skip(1).collect::<Vec<_>>();
    if args.is_empty() {
        usage();
        return ExitCode::from(2);
    }

    match args.remove(0).as_str() {
        "run" => run_command(args),
        "bench-lme" => bench_lme_command(args),
        "embed-pool" => pool_tool_command(pool_tools::embed_pool_command(&args)),
        "rerank-pool" => pool_tool_command(pool_tools::rerank_pool_command(&args)),
        "verify-golden" => verify_golden_command(args),
        "security" => security_command(args),
        "ops" => ops_command(args),
        "syndai-trace-compare" => syndai_trace_compare_command(args),
        "schema" => schema_command(args),
        "ablate" => ablate_command(args),
        "profile" => profile_command(args),
        _ => {
            usage();
            ExitCode::from(2)
        }
    }
}

fn pool_tool_command(result: Result<(), String>) -> ExitCode {
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("{error}");
            ExitCode::from(2)
        }
    }
}

fn run_command(args: Vec<String>) -> ExitCode {
    let Some(path) = args.first() else {
        usage();
        return ExitCode::from(2);
    };
    let mut archive_traces = false;
    let mut archive_dir = None;
    let mut contextual_chunks_enabled = true;
    let mut temporal_validity_enabled = true;
    let mut edge_expansion_enabled = true;
    let mut context_packing_abstention_enabled = true;
    let mut procedure_recall_enabled = true;
    let mut decay_enabled = true;
    let mut l4_exhaustive_enabled = true;
    let mut l4_runtime_provider = false;
    let mut filesystem_control_enabled = false;
    let mut index = 1;
    while index < args.len() {
        match args[index].as_str() {
            "--archive-traces" => {
                archive_traces = true;
                index += 1;
            }
            "--disable-contextual-chunks" => {
                contextual_chunks_enabled = false;
                index += 1;
            }
            "--disable-temporal-validity" => {
                temporal_validity_enabled = false;
                index += 1;
            }
            "--disable-edge-expansion" => {
                edge_expansion_enabled = false;
                index += 1;
            }
            "--disable-context-packing-abstention" => {
                context_packing_abstention_enabled = false;
                index += 1;
            }
            "--disable-procedure-recall" => {
                procedure_recall_enabled = false;
                index += 1;
            }
            "--disable-decay" => {
                decay_enabled = false;
                index += 1;
            }
            "--disable-l4-exhaustive" => {
                l4_exhaustive_enabled = false;
                index += 1;
            }
            "--l4-runtime-provider" => {
                l4_runtime_provider = true;
                index += 1;
            }
            "--filesystem-control" => {
                edge_expansion_enabled = false;
                filesystem_control_enabled = true;
                index += 1;
            }
            "--archive-dir" if index + 1 < args.len() => {
                archive_dir = Some(PathBuf::from(&args[index + 1]));
                index += 2;
            }
            _ => {
                usage();
                return ExitCode::from(2);
            }
        }
    }

    match run_eval_file(
        &PathBuf::from(path),
        EvalRunOptions {
            archive_traces,
            archive_dir,
            contextual_chunks_enabled,
            temporal_validity_enabled,
            edge_expansion_enabled,
            context_packing_abstention_enabled,
            procedure_recall_enabled,
            decay_enabled,
            l4_exhaustive_enabled,
            l4_runtime_provider,
            filesystem_control_enabled,
        },
    ) {
        Ok(report) if report.passed_cases == report.total_cases => {
            println!(
                "eval=pass id={} passed={}/{} archive={}",
                report.eval_id,
                report.passed_cases,
                report.total_cases,
                report
                    .archived_trace_path
                    .as_ref()
                    .map(|path| path.display().to_string())
                    .unwrap_or_else(|| "none".to_string())
            );
            print_deep_settlements(&report);
            ExitCode::SUCCESS
        }
        Ok(report) => {
            eprintln!(
                "eval=fail id={} passed={}/{}",
                report.eval_id, report.passed_cases, report.total_cases
            );
            for case in report.case_results.iter().filter(|case| !case.passed) {
                eprintln!("case={} error={:?}", case.id, case.error);
            }
            print_deep_settlements(&report);
            ExitCode::from(1)
        }
        Err(error) => {
            eprintln!("eval=error");
            eprintln!("{error}");
            ExitCode::from(1)
        }
    }
}

fn verify_golden_command(args: Vec<String>) -> ExitCode {
    let Some(path) = args.first() else {
        usage();
        return ExitCode::from(2);
    };
    match verify_golden_file(&PathBuf::from(path)) {
        Ok(report) if report.case_results.iter().all(|case| case.load_bearing) => {
            println!("verify_golden=pass cases={}", report.verified_cases);
            ExitCode::SUCCESS
        }
        Ok(report) => {
            eprintln!("verify_golden=fail cases={}", report.verified_cases);
            for case in report.case_results.iter().filter(|case| !case.load_bearing) {
                eprintln!("case={} reason={}", case.id, case.reason);
            }
            ExitCode::from(1)
        }
        Err(error) => {
            eprintln!("verify_golden=error");
            eprintln!("{error}");
            ExitCode::from(1)
        }
    }
}

fn security_command(args: Vec<String>) -> ExitCode {
    let Some(path) = args.first() else {
        usage();
        return ExitCode::from(2);
    };
    match run_security_file(&PathBuf::from(path)) {
        Ok(report) if report.passed => {
            println!(
                "security=pass lanes={} deletion_completeness=pass",
                report.covered_lanes.join(",")
            );
            ExitCode::SUCCESS
        }
        Ok(report) => {
            eprintln!("security=fail id={}", report.id);
            for lane in report.lane_results.iter().filter(|lane| !lane.passed) {
                eprintln!("lane={} kind={} detail={}", lane.id, lane.kind, lane.detail);
            }
            ExitCode::from(1)
        }
        Err(error) => {
            eprintln!("security=error");
            eprintln!("{error}");
            ExitCode::from(1)
        }
    }
}

fn ops_command(args: Vec<String>) -> ExitCode {
    let Some(path) = args.first() else {
        usage();
        return ExitCode::from(2);
    };
    match run_ops_file(&PathBuf::from(path)) {
        Ok(report) if report.passed => {
            println!("ops=pass checks={}", report.covered_checks.join(","));
            ExitCode::SUCCESS
        }
        Ok(report) => {
            eprintln!("ops=fail id={}", report.id);
            for check in report.check_results.iter().filter(|check| !check.passed) {
                eprintln!(
                    "check={} kind={} detail={}",
                    check.id, check.kind, check.detail
                );
            }
            ExitCode::from(1)
        }
        Err(error) => {
            eprintln!("ops=error");
            eprintln!("{error}");
            ExitCode::from(1)
        }
    }
}

fn syndai_trace_compare_command(args: Vec<String>) -> ExitCode {
    let Some(path) = args.first() else {
        usage();
        return ExitCode::from(2);
    };
    let mut archive_traces = false;
    let mut archive_dir = None;
    let mut index = 1;
    while index < args.len() {
        match args[index].as_str() {
            "--archive-traces" => {
                archive_traces = true;
                index += 1;
            }
            "--archive-dir" if index + 1 < args.len() => {
                archive_dir = Some(PathBuf::from(&args[index + 1]));
                index += 2;
            }
            _ => {
                usage();
                return ExitCode::from(2);
            }
        }
    }
    match run_syndai_trace_compare_file(
        &PathBuf::from(path),
        EvalRunOptions {
            archive_traces,
            archive_dir,
            contextual_chunks_enabled: true,
            temporal_validity_enabled: true,
            edge_expansion_enabled: true,
            context_packing_abstention_enabled: true,
            procedure_recall_enabled: true,
            decay_enabled: true,
            l4_exhaustive_enabled: true,
            l4_runtime_provider: false,
            filesystem_control_enabled: false,
        },
    ) {
        Ok(report) if report.passed => {
            println!(
                "syndai_trace_compare=pass id={} surface={} recall={} archive={}",
                report.id,
                report.surface,
                report.answer_bearing_recall,
                report
                    .archived_trace_path
                    .as_ref()
                    .map(|path| path.display().to_string())
                    .unwrap_or_else(|| "none".to_string())
            );
            ExitCode::SUCCESS
        }
        Ok(report) => {
            eprintln!(
                "syndai_trace_compare=fail id={} missing={:?} forbidden={:?} other={:?}",
                report.id,
                report.missing_answer_bearing,
                report.forbidden_returned,
                report.other_mismatches
            );
            ExitCode::from(1)
        }
        Err(error) => {
            eprintln!("syndai_trace_compare=error");
            eprintln!("{error}");
            ExitCode::from(1)
        }
    }
}

fn schema_command(args: Vec<String>) -> ExitCode {
    if args.as_slice() != ["trace"] {
        usage();
        return ExitCode::from(2);
    }
    match serde_json::to_string_pretty(&generate_trace_schema()) {
        Ok(json) => {
            println!("{json}");
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("schema=error");
            eprintln!("{error}");
            ExitCode::from(1)
        }
    }
}

fn ablate_command(args: Vec<String>) -> ExitCode {
    let Some(path) = args.first() else {
        usage();
        return ExitCode::from(2);
    };
    match run_eval_file(&PathBuf::from(path), EvalRunOptions::default()) {
        Ok(report) if report.passed_cases == report.total_cases => {
            println!(
                "ablate=pass id={} deterministic_baseline_delta=0.0",
                report.eval_id
            );
            ExitCode::SUCCESS
        }
        Ok(report) => {
            eprintln!("ablate=fail id={}", report.eval_id);
            ExitCode::from(1)
        }
        Err(error) => {
            eprintln!("ablate=error");
            eprintln!("{error}");
            ExitCode::from(1)
        }
    }
}

fn profile_command(args: Vec<String>) -> ExitCode {
    let Some(path) = args.first() else {
        usage();
        return ExitCode::from(2);
    };
    let mut compare_to = None;
    let mut archive = None;
    let mut index = 1;
    while index < args.len() {
        match args[index].as_str() {
            "--compare-to" if index + 1 < args.len() => {
                compare_to = Some(args[index + 1].clone());
                index += 2;
            }
            "--archive" if index + 1 < args.len() => {
                archive = Some(PathBuf::from(&args[index + 1]));
                index += 2;
            }
            _ => {
                usage();
                return ExitCode::from(2);
            }
        }
    }
    let Some(compare_to) = compare_to else {
        usage();
        return ExitCode::from(2);
    };

    match run_profile_file(&PathBuf::from(path), &compare_to, archive) {
        Ok(report) => {
            println!(
                "profile=pass id={} compare_to={} activated={} dormant={} retired={} archive={}",
                report.id,
                report.compare_to,
                report.activated_levers.len(),
                report.dormant_levers.len(),
                report.retired_levers.len(),
                report
                    .archived_path
                    .as_ref()
                    .map(|path| path.display().to_string())
                    .unwrap_or_else(|| "none".to_string())
            );
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("profile=error");
            eprintln!("{error}");
            ExitCode::from(1)
        }
    }
}

fn bench_lme_command(args: Vec<String>) -> ExitCode {
    let mut database_url = None;
    let mut data = None;
    let mut sample = None;
    let mut seed = None;
    let mut k = 10usize;
    let mut disable = None;
    let mut mode = memphant_types::RecallMode::Fast;
    let mut baseline = None;
    let mut out = None;
    let mut granularity = memphant_eval::bench_lme::DEFAULT_GRANULARITY.to_string();
    let mut turns_window = memphant_eval::bench_lme::DEFAULT_TURNS_WINDOW;
    let mut budget_tokens = memphant_eval::bench_lme::DEFAULT_BUDGET_TOKENS;
    let mut pool = memphant_core::DEFAULT_RECALL_POOL_DEPTH;
    // W4 packing levers: default off (the campaign toggles each). See
    // `MemoryService::with_session_quota`.
    let mut session_quota: Option<usize> = None;
    let mut pack_render_cap: Option<usize> = None;
    let mut lexical_scorer = memphant_core::LexicalScorer::Overlap;
    let mut pack_submodular_ordering = false;
    // W5 temporal grounding: default off (measurement-only promotion). See
    // `MemoryService::with_temporal_grounding_enabled`.
    let mut temporal_grounding = false;
    // W6 deterministic fact extraction: default off (measurement-only). See
    // `MemoryService::with_fact_extraction_enabled`.
    let mut fact_extraction = false;
    // Default-on: the lane measures the product path (service-side runtime
    // contextual chunks). `--disable runtime_chunks` runs the chunks-off
    // control arm; `--runtime-chunks` is a now-redundant explicit opt-in.
    let mut runtime_chunks = true;
    // W8 embedding arm (default small = unchanged) and cross-encoder rerank
    // (default off = today's fusion order).
    let mut embed_model = "small".to_string();
    let mut cross_rerank = false;
    let mut rerank_granularity = memphant_core::CrossRerankGranularity::UnitBody;
    let mut emit_qa = None;
    let mut emit_trace_classification = None;
    let mut index = 0;
    while index < args.len() {
        let take = |index: usize| -> Option<String> { args.get(index + 1).cloned() };
        match args[index].as_str() {
            "--database-url" => {
                database_url = take(index);
                index += 2;
            }
            "--data" => {
                data = take(index);
                index += 2;
            }
            "--sample" => {
                sample = take(index).and_then(|value| value.parse::<usize>().ok());
                index += 2;
            }
            "--seed" => {
                seed = take(index).and_then(|value| value.parse::<u64>().ok());
                index += 2;
            }
            "--k" => {
                match take(index).and_then(|value| value.parse::<usize>().ok()) {
                    Some(value) => k = value,
                    None => {
                        usage();
                        return ExitCode::from(2);
                    }
                }
                index += 2;
            }
            "--disable" => {
                disable = take(index);
                index += 2;
            }
            "--mode" => {
                mode = match take(index).as_deref() {
                    Some("fast") => memphant_types::RecallMode::Fast,
                    Some("deep") => memphant_types::RecallMode::Deep,
                    _ => {
                        usage();
                        return ExitCode::from(2);
                    }
                };
                index += 2;
            }
            "--baseline" => {
                baseline = take(index);
                index += 2;
            }
            "--granularity" => {
                match take(index).as_deref() {
                    Some("session") => granularity = "session".to_string(),
                    Some("turns") => granularity = "turns".to_string(),
                    _ => {
                        usage();
                        return ExitCode::from(2);
                    }
                }
                index += 2;
            }
            "--turns-window" => {
                match take(index).and_then(|value| value.parse::<usize>().ok()) {
                    Some(0) => {
                        eprintln!("bench_lme=error\n--turns-window must be > 0");
                        return ExitCode::from(2);
                    }
                    Some(value) => turns_window = value,
                    None => {
                        usage();
                        return ExitCode::from(2);
                    }
                }
                index += 2;
            }
            "--budget-tokens" => {
                match take(index).and_then(|value| value.parse::<usize>().ok()) {
                    Some(0) => {
                        eprintln!("bench_lme=error\n--budget-tokens must be > 0");
                        return ExitCode::from(2);
                    }
                    Some(value) => budget_tokens = value,
                    None => {
                        usage();
                        return ExitCode::from(2);
                    }
                }
                index += 2;
            }
            "--pool" => {
                match take(index).and_then(|value| value.parse::<usize>().ok()) {
                    Some(0) => {
                        eprintln!(
                            "bench_lme=error\n--pool must be > 0 (use --disable vector to turn the vector channel off)"
                        );
                        return ExitCode::from(2);
                    }
                    Some(value) => pool = value,
                    None => {
                        usage();
                        return ExitCode::from(2);
                    }
                }
                index += 2;
            }
            "--session-quota" => {
                match take(index).and_then(|value| value.parse::<usize>().ok()) {
                    Some(0) => {
                        eprintln!(
                            "bench_lme=error\n--session-quota must be > 0 (omit the flag to leave the quota off)"
                        );
                        return ExitCode::from(2);
                    }
                    Some(value) => session_quota = Some(value),
                    None => {
                        usage();
                        return ExitCode::from(2);
                    }
                }
                index += 2;
            }
            "--pack-render-cap" => {
                match take(index).and_then(|value| value.parse::<usize>().ok()) {
                    Some(0) => {
                        eprintln!(
                            "bench_lme=error\n--pack-render-cap must be > 0 (omit the flag to leave the cap off)"
                        );
                        return ExitCode::from(2);
                    }
                    Some(value) => pack_render_cap = Some(value),
                    None => {
                        usage();
                        return ExitCode::from(2);
                    }
                }
                index += 2;
            }
            "--lexical-scorer" => {
                match take(index).as_deref() {
                    Some("overlap") => lexical_scorer = memphant_core::LexicalScorer::Overlap,
                    Some("bm25-control") => {
                        lexical_scorer = memphant_core::LexicalScorer::Bm25Control
                    }
                    Some("bm25-code") => lexical_scorer = memphant_core::LexicalScorer::Bm25Code,
                    _ => {
                        usage();
                        return ExitCode::from(2);
                    }
                }
                index += 2;
            }
            "--pack-submodular-ordering" => {
                pack_submodular_ordering = true;
                index += 1;
            }
            "--runtime-chunks" => {
                runtime_chunks = true;
                index += 1;
            }
            "--temporal-grounding" => {
                temporal_grounding = true;
                index += 1;
            }
            "--fact-extraction" => {
                fact_extraction = true;
                index += 1;
            }
            "--embed-model" => {
                // The id is validated by the shared `embedder_from_id` grammar
                // at build time (single source of truth), so we accept any
                // non-flag value here and reject only a missing/flag-like one.
                match take(index) {
                    Some(value) if !value.starts_with("--") => embed_model = value,
                    _ => {
                        usage();
                        return ExitCode::from(2);
                    }
                }
                index += 2;
            }
            "--cross-rerank" => {
                cross_rerank = true;
                index += 1;
            }
            "--rerank-granularity" => {
                rerank_granularity = match take(index).as_deref() {
                    Some("body") => memphant_core::CrossRerankGranularity::UnitBody,
                    Some("chunk") => memphant_core::CrossRerankGranularity::ContextualChunks,
                    _ => {
                        usage();
                        return ExitCode::from(2);
                    }
                };
                index += 2;
            }
            "--emit-qa" => {
                emit_qa = take(index);
                index += 2;
            }
            "--emit-trace-classification" => {
                emit_trace_classification = take(index);
                index += 2;
            }
            "--out" => {
                out = take(index).map(PathBuf::from);
                index += 2;
            }
            _ => {
                usage();
                return ExitCode::from(2);
            }
        }
    }
    let (Some(database_url), Some(data), Some(sample), Some(seed)) =
        (database_url, data, sample, seed)
    else {
        usage();
        return ExitCode::from(2);
    };

    let command = std::env::args().collect::<Vec<_>>().join(" ");
    let options = memphant_eval::bench_lme::BenchLmeOptions {
        database_url,
        data_path: data,
        sample,
        seed,
        k,
        disable,
        mode,
        baseline,
        granularity,
        turns_window,
        budget_tokens,
        pool,
        session_quota,
        pack_render_cap,
        lexical_scorer,
        pack_submodular_ordering,
        temporal_grounding,
        fact_extraction,
        runtime_chunks,
        embed_model,
        cross_rerank,
        rerank_granularity,
        emit_qa,
        emit_trace_classification,
        command,
    };
    match memphant_eval::bench_lme::run_bench_lme(&options) {
        Ok(report) => {
            let json = match serde_json::to_string_pretty(&report) {
                Ok(json) => json,
                Err(error) => {
                    eprintln!("bench_lme=error\n{error}");
                    return ExitCode::from(1);
                }
            };
            match &out {
                Some(path) => {
                    if let Err(error) = std::fs::write(path, format!("{json}\n")) {
                        eprintln!("bench_lme=error\n{error}");
                        return ExitCode::from(1);
                    }
                }
                None => println!("{json}"),
            }
            println!(
                "bench_lme=done sample={} seed={} recall_at_5={:?} recall_at_10={:?} disabled={} out={}",
                report.sample_n,
                report.sample_seed,
                report.overall.recall_at_5,
                report.overall.recall_at_10,
                report.disabled.as_deref().unwrap_or("none"),
                out.as_ref()
                    .map(|path| path.display().to_string())
                    .unwrap_or_else(|| "stdout".to_string())
            );
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("bench_lme=error\n{error}");
            ExitCode::from(1)
        }
    }
}

fn usage() {
    eprintln!(
        "usage: memphant-eval bench-lme --database-url <url> --data <longmemeval.json> --sample <n> --seed <s> [--k 10] [--disable vector|edge_expansion|procedure_recall|decay|packing|runtime_chunks] [--mode fast|deep] [--granularity turns|session (default: session)] [--turns-window <n> (default: 4)] [--budget-tokens <n> (default: 8192)] [--pool <n> (default: 64; recall-pool-depth — the ONE knob every internal channel/fusion limit in the recall path derives from, never k)] [--session-quota <n> (default: off; W4 per-session diversity cap)] [--pack-render-cap <n> (default: off; rung-7 per-item render-token cap)] [--lexical-scorer overlap|bm25-control|bm25-code (default: overlap; fusion's lexical family)] [--pack-submodular-ordering (default: off; relevance+coverage+representativeness+diversity)] [--temporal-grounding (default: off; W5 content-date grounding + windowed recall + dated packs)] [--fact-extraction (default: off; W6 deterministic preference/attribute fact mining at reflect)] [--runtime-chunks (default: on; --disable runtime_chunks for the control arm)] [--embed-model small|base|modernbert|gemma|qwen3|voyage-4|voyage-4-lite|voyage-4-large|voyage-code-3|voyage-context-4|gemini-embedding-001|openai-text-embedding-3-small (default: small; W8/R0 embedding arm; qwen3 requires --features qwen3; voyage/gemini/openai arms read the provider API key from env: VOYAGE_API_KEY/GEMINI_API_KEY/OPENAI_API_KEY)] [--cross-rerank (default: off; W8 cross-encoder rerank over the candidate pool, requires --features fastembed)] [--rerank-granularity body|chunk (default: body; cross-rerank doc granularity — chunk reranks contextual_chunks and max-pools per candidate; inert without --cross-rerank)] [--emit-qa <evidence.jsonl>] [--emit-trace-classification <classification.jsonl> (A1: FREE Fast-miss bucket per question from the retrieval trace)] [--baseline <report.json>] [--out <report.json>] | memphant-eval run <suite.yaml> [--archive-traces] [--archive-dir <dir>] [--disable-contextual-chunks] [--disable-temporal-validity] [--disable-edge-expansion] [--disable-context-packing-abstention] [--disable-procedure-recall] [--disable-decay] [--disable-l4-exhaustive] [--l4-runtime-provider (paid ignored rung only; reads strict Deep env)] [--filesystem-control] | memphant-eval verify-golden <suite.yaml> | memphant-eval security <suite.yaml> | memphant-eval ops <suite.yaml> | memphant-eval syndai-trace-compare <fixture.yaml> [--archive-traces] [--archive-dir <dir>] | memphant-eval profile <profile.yaml> --compare-to <baseline> [--archive <path>] | memphant-eval schema trace"
    );
}
