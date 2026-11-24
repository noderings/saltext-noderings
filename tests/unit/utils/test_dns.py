import pytest

from saltext.noderings.utils import dns


@pytest.mark.parametrize(
    "rrtype,a,b,same",
    [
        ("A", ["203.0.113.1", "203.0.113.2"], ["203.0.113.2", "203.0.113.1"], True),
        ("CNAME", ["Target.example.com"], ["target.example.com."], True),
        ("MX", ["10 mail.example.com"], ["10 mail.example.com."], True),
        ("TXT", ["Hello"], ["hello"], False),
        ("AAAA", ["2001:db8::1"], ["2001:0db8:0:0::1"], True),
    ],
)
def test_dns_same_values(rrtype, a, b, same):
    assert dns.same_values(rrtype, a, b) is same


def test_dns_names_and_txt():
    assert dns.relative_name("www.Example.com.", "example.com") == "www"
    assert dns.relative_name("example.com", "example.com") == "@"
    assert dns.relative_name("", "example.com") == "@"
    long_value = "x" * 300 + ' "quoted"'
    quoted = dns.quote_txt(long_value)
    assert quoted.count('" "') == 1
    assert dns.unquote_txt(quoted) == long_value
    assert dns.validate_values("CNAME", ["a.", "b."])
    assert dns.validate_values("A", ["::1"])
    assert dns.validate_values("A", ["192.0.2.1"]) is None
