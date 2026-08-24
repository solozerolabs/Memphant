/// Lowercases `input`, replaces each run of non-alphanumeric characters with a
/// single hyphen, and trims leading/trailing hyphens.
pub fn slugify(input: &str) -> String {
    let mut slug = String::with_capacity(input.len());
    let mut pending_separator = false;

    for ch in input.chars() {
        if ch.is_alphanumeric() {
            if pending_separator && !slug.is_empty() {
                slug.push('-');
            }
            pending_separator = false;
            slug.extend(ch.to_lowercase());
        } else {
            pending_separator = true;
        }
    }

    slug
}

#[cfg(test)]
mod tests {
    use super::slugify;

    #[test]
    fn lowercases_and_hyphenates_punctuation() {
        assert_eq!(slugify("Hello, World!"), "hello-world");
    }

    #[test]
    fn collapses_repeated_separators_and_trims_whitespace() {
        assert_eq!(slugify("  Rust__Lang  "), "rust-lang");
    }

    #[test]
    fn collapses_repeated_hyphens() {
        assert_eq!(slugify("a---b"), "a-b");
    }

    #[test]
    fn empty_input_yields_empty_slug() {
        assert_eq!(slugify(""), "");
    }
}
