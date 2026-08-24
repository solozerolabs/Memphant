pub fn slugify(text: &str) -> String {
    let mut slug = String::with_capacity(text.len());
    let mut last_was_hyphen = true;
    for ch in text.chars() {
        if ch.is_ascii_alphanumeric() {
            slug.push(ch.to_ascii_lowercase());
            last_was_hyphen = false;
        } else if !last_was_hyphen {
            slug.push('-');
            last_was_hyphen = true;
        }
    }
    if slug.ends_with('-') {
        slug.pop();
    }
    slug
}

#[cfg(test)]
mod tests {
    use super::slugify;

    #[test]
    fn lowercases_and_replaces_punctuation() {
        assert_eq!(slugify("Hello, World!"), "hello-world");
    }

    #[test]
    fn collapses_runs_and_trims_edges() {
        assert_eq!(slugify("  Foo___Bar  "), "foo-bar");
    }

    #[test]
    fn single_char_is_unchanged() {
        assert_eq!(slugify("a"), "a");
    }

    #[test]
    fn empty_input_is_empty_output() {
        assert_eq!(slugify(""), "");
    }
}
