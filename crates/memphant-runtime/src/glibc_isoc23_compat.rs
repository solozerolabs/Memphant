//! Link-time shim for glibc hosts older than 2.38.
//!
//! `fastembed`'s bundled onnxruntime binary (via `ort-sys`'s
//! `download-binaries`) was built against a glibc that exports the ISO C23
//! `strtol`/`strtoll`/`strtoull` variants (`__isoc23_*`, added in glibc
//! 2.38). On older glibc the dynamic linker has nothing to satisfy those
//! references, which fails the link of every binary that pulls in
//! `fastembed` by default (server/worker/mcp). The `__isoc23_*` entry points
//! only change behavior for C23's extra base-prefix autodetection
//! (e.g. `0b`); onnxruntime's usage is plain explicit-base integer parsing,
//! so forwarding to the classic `strtol`/`strtoll`/`strtoull` is behavior
//! preserving. On hosts where glibc already provides `__isoc23_*` natively,
//! these definitions simply take priority at static-link time and forward to
//! the same underlying libc routine.
unsafe extern "C" {
    fn strtol(nptr: *const i8, endptr: *mut *mut i8, base: i32) -> i64;
    fn strtoll(nptr: *const i8, endptr: *mut *mut i8, base: i32) -> i64;
    fn strtoull(nptr: *const i8, endptr: *mut *mut i8, base: i32) -> u64;
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn __isoc23_strtol(nptr: *const i8, endptr: *mut *mut i8, base: i32) -> i64 {
    unsafe { strtol(nptr, endptr, base) }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn __isoc23_strtoll(nptr: *const i8, endptr: *mut *mut i8, base: i32) -> i64 {
    unsafe { strtoll(nptr, endptr, base) }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn __isoc23_strtoull(
    nptr: *const i8,
    endptr: *mut *mut i8,
    base: i32,
) -> u64 {
    unsafe { strtoull(nptr, endptr, base) }
}
