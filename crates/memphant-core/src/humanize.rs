const UNITS: [&str; 9] = ["B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB", "ZiB", "YiB"];

pub fn humanize_bytes(n: u64) -> String {
    if n == 0 {
        return "0 B".to_string();
    }

    let mut value = n as f64;
    let mut unit_index = 0;
    while value >= 1024.0 && unit_index < UNITS.len() - 1 {
        value /= 1024.0;
        unit_index += 1;
    }

    if unit_index == 0 {
        format!("{n} B")
    } else {
        format!("{value:.1} {}", UNITS[unit_index])
    }
}

#[cfg(test)]
mod tests {
    use super::humanize_bytes;

    #[test]
    fn formats_iec_byte_sizes() {
        assert_eq!(humanize_bytes(0), "0 B");
        assert_eq!(humanize_bytes(512), "512 B");
        assert_eq!(humanize_bytes(1536), "1.5 KiB");
        assert_eq!(humanize_bytes(1024 * 1024), "1.0 MiB");
        assert_eq!(humanize_bytes(1024 * 1024 * 1024 * 3), "3.0 GiB");
    }
}
