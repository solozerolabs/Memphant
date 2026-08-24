/// Lowercases `input`, collapses every run of non-alphanumeric characters
/// into a single hyphen, and trims leading/trailing hyphens.
pub fn slugify(input: &str) -> String {
    let mut result = String::with_capacity(input.len());
    let mut pending_hyphen = false;

    for ch in input.chars() {
        if ch.is_alphanumeric() {
            if pending_hyphen {
                if !result.is_empty() {
                    result.push('-');
                }
                pending_hyphen = false;
            }
            result.extend(ch.to_lowercase());
        } else {
            pending_hyphen = true;
        }
    }

    result
}

#[cfg(test)]
mod tests {
    use super::slugify;

    #[test]
    fn lowercases_and_replaces_punctuation() {
        assert_eq!(slugify("Hello, World!"), "hello-world");
    }

    #[test]
    fn collapses_runs_and_trims_surrounding_whitespace() {
        assert_eq!(slugify("  Rust__Lang  "), "rust-lang");
    }

    #[test]
    fn collapses_repeated_hyphens() {
        assert_eq!(slugify("a---b"), "a-b");
    }

    #[test]
    fn empty_input_yields_empty_output() {
        assert_eq!(slugify(""), "");
    }
}
