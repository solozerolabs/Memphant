/// Collapses every run of ASCII whitespace in `input` to a single space and
/// trims leading and trailing whitespace.
pub fn normalize_whitespace(input: &str) -> String {
    input.split_ascii_whitespace().collect::<Vec<_>>().join(" ")
}

#[cfg(test)]
mod tests {
    use super::normalize_whitespace;

    #[test]
    fn collapses_interior_runs_and_trims_ends() {
        assert_eq!(normalize_whitespace("  a   b  "), "a b");
    }

    #[test]
    fn empty_input_returns_empty_string() {
        assert_eq!(normalize_whitespace(""), "");
    }

    #[test]
    fn single_token_is_unchanged() {
        assert_eq!(normalize_whitespace("x"), "x");
    }

    #[test]
    fn mixed_whitespace_kinds_collapse() {
        assert_eq!(normalize_whitespace("a\t\nb\r\nc"), "a b c");
    }
}
