pub fn slugify(text: &str) -> String {
    let mut slug = String::with_capacity(text.len());
    let mut prev_hyphen = true;
    for ch in text.chars() {
        if ch.is_ascii_alphanumeric() {
            slug.push(ch.to_ascii_lowercase());
            prev_hyphen = false;
        } else if !prev_hyphen {
            slug.push('-');
            prev_hyphen = true;
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
    fn lowercases_and_hyphenates() {
        assert_eq!(slugify("Hello, World!"), "hello-world");
    }

    #[test]
    fn collapses_runs_and_trims() {
        assert_eq!(slugify("  Foo___Bar  "), "foo-bar");
    }

    #[test]
    fn single_char() {
        assert_eq!(slugify("a"), "a");
    }

    #[test]
    fn empty_string() {
        assert_eq!(slugify(""), "");
    }
}
