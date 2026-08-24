/// Lowercases `input`, collapses every run of non-alphanumeric characters
/// into a single hyphen, and trims leading/trailing hyphens.
pub fn slugify(input: &str) -> String {
    let mut slug = String::with_capacity(input.len());
    let mut pending_hyphen = false;

    for ch in input.chars() {
        if ch.is_alphanumeric() {
            if pending_hyphen && !slug.is_empty() {
                slug.push('-');
            }
            pending_hyphen = false;
            slug.extend(ch.to_lowercase());
        } else {
            pending_hyphen = true;
        }
    }

    slug
}

#[cfg(test)]
mod tests {
    use super::slugify;

    #[test]
    fn punctuation_becomes_hyphens() {
        assert_eq!(slugify("Hello, World!"), "hello-world");
    }

    #[test]
    fn collapses_runs_and_trims_whitespace() {
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
