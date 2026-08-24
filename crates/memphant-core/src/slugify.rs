pub fn slugify(text: &str) -> String {
    let mut slug = String::with_capacity(text.len());
    let mut pending_hyphen = false;

    for ch in text.chars() {
        if ch.is_ascii_alphanumeric() {
            if pending_hyphen && !slug.is_empty() {
                slug.push('-');
            }
            pending_hyphen = false;
            slug.push(ch.to_ascii_lowercase());
        } else {
            pending_hyphen = true;
        }
    }

    slug
}

#[cfg(test)]
mod slugify_tests {
    use super::slugify;

    #[test]
    fn replaces_punctuation_and_lowercases() {
        assert_eq!(slugify("Hello, World!"), "hello-world");
    }

    #[test]
    fn collapses_runs_and_trims_surrounding_whitespace() {
        assert_eq!(slugify("  Foo___Bar  "), "foo-bar");
    }

    #[test]
    fn passes_through_single_alphanumeric_char() {
        assert_eq!(slugify("a"), "a");
    }

    #[test]
    fn empty_input_yields_empty_output() {
        assert_eq!(slugify(""), "");
    }
}
