/// Collapses every run of ASCII whitespace in `input` to a single space and
/// trims leading and trailing whitespace.
pub fn normalize_whitespace(input: &str) -> String {
    input.split_ascii_whitespace().collect::<Vec<_>>().join(" ")
}

#[cfg(test)]
mod tests {
    use super::normalize_whitespace;

    #[test]
    fn collapses_internal_runs_and_trims_edges() {
        assert_eq!(normalize_whitespace("  a   b  "), "a b");
    }

    #[test]
    fn empty_input_returns_empty_string() {
        assert_eq!(normalize_whitespace(""), "");
    }

    #[test]
    fn single_word_is_unchanged() {
        assert_eq!(normalize_whitespace("x"), "x");
    }

    #[test]
    fn collapses_mixed_whitespace_kinds() {
        assert_eq!(normalize_whitespace("a\t\nb\r\nc"), "a b c");
    }
}
