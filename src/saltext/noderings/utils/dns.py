"""
DNS name and record-value helpers.
"""

import ipaddress

RECORD_TYPES = [
    "A",
    "AAAA",
    "ALIAS",
    "CAA",
    "CERT",
    "CNAME",
    "DNAME",
    "DS",
    "HINFO",
    "HTTPS",
    "LOC",
    "MX",
    "NAPTR",
    "NS",
    "OPENPGPKEY",
    "PTR",
    "RP",
    "SMIMEA",
    "SRV",
    "SSHFP",
    "SVCB",
    "TLSA",
    "TXT",
]
GEO_RECORD_TYPES = ["A", "AAAA", "CNAME", "NAPTR", "SRV"]
SINGLE_VALUE_TYPES = frozenset(["CNAME", "DNAME", "ALIAS"])


def normalize_zone_name(name):
    return (name or "").strip().lower().rstrip(".")


def relative_name(name, zone):
    """www.example.com. in example.com -> www; the apex -> @."""
    n = (name or "").strip().lower()
    z = normalize_zone_name(zone)
    if n in ("", "@"):
        return "@"
    n = n.rstrip(".")
    if z:
        if n == z:
            return "@"
        if n.endswith(f".{z}"):
            n = n[: -len(z) - 1]
    return n


def validate_values(rrtype, values):
    """Return an error message, or None when the values are acceptable."""
    if not values:
        return "at least one record value is required"
    if rrtype in SINGLE_VALUE_TYPES and len(values) > 1:
        return f"{rrtype} records take exactly one value"
    for v in values:
        v = str(v)
        if not v.strip():
            return "record values must not be empty"
        if rrtype in ("A", "AAAA", "ALIAS"):
            try:
                ip = ipaddress.ip_address(v.strip())
            except ValueError:
                ip = None
            if rrtype == "A" and (ip is None or ip.version != 4):
                return f"{v!r} is not an IPv4 address"
            if rrtype == "AAAA" and (ip is None or ip.version != 6 or ip.ipv4_mapped):
                return f"{v!r} is not an IPv6 address"
            if rrtype == "ALIAS" and ip is not None:
                return "ALIAS must point to a hostname, not an address"
        if rrtype == "TXT" and len(v) > 4096:
            return "TXT values are limited to 4096 characters"
    return None


def _fqdn(h):
    h = (h or "").strip().lower()
    if h in ("", "."):
        return h
    return f"{h.rstrip('.')}."


def canonical(rrtype, v):
    """A comparable form of one value: the API may lowercase names or add trailing dots."""
    v = str(v).strip()
    if rrtype == "TXT":
        return v
    if rrtype in ("A", "AAAA"):
        try:
            return str(ipaddress.ip_address(v))
        except ValueError:
            return v.lower()
    if rrtype in ("CNAME", "DNAME", "ALIAS", "NS", "PTR"):
        return _fqdn(v)
    f = v.split()
    if rrtype == "MX" and len(f) == 2:
        return f"{f[0]} {_fqdn(f[1])}"
    if rrtype == "SRV" and len(f) == 4:
        return f"{' '.join(f[:3])} {_fqdn(f[3])}"
    return " ".join(v.lower().split())


def same_values(rrtype, a, b):
    return sorted(canonical(rrtype, x) for x in a or []) == sorted(
        canonical(rrtype, x) for x in b or []
    )


def _utf8_start(b):
    return (b & 0xC0) != 0x80


def quote_txt(v):
    """Split a TXT value into 255-byte quoted strings, as the API expects."""
    raw = str(v).encode("utf-8")
    parts = []
    while len(raw) > 255:
        cut = 255
        while cut > 0 and not _utf8_start(raw[cut]):
            cut -= 1
        parts.append(raw[:cut])
        raw = raw[cut:]
    parts.append(raw)
    out = []
    for p in parts:
        s = p.decode("utf-8").replace("\\", "\\\\").replace('"', '\\"')
        out.append(f'"{s}"')
    return " ".join(out)


def unquote_txt(v):
    """Join the API's quoted TXT strings back into one value."""
    v = str(v).strip()
    if not v.startswith('"'):
        return v
    out = []
    inside = False
    i = 0
    while i < len(v):
        c = v[i]
        if c == '"':
            inside = not inside
        elif c == "\\" and inside and i + 1 < len(v):
            i += 1
            out.append(v[i])
        elif inside:
            out.append(c)
        i += 1
    return "".join(out)


def to_api(rrtype, v):
    return quote_txt(v) if rrtype == "TXT" else str(v).strip()


def from_api(rrtype, v):
    return unquote_txt(v) if rrtype == "TXT" else str(v).strip()
