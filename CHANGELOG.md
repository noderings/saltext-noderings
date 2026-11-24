# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and the project uses [Semantic Versioning](https://semver.org/).

## 0.1.0 (unreleased)

### Added

- Execution module `noderings`: virtual machines (order, start, stop, restart, reinstall, tags, firewall), DNS zones and records with DNSSEC, firewall rulesets with ordered rules, SSH keys, and the marketplace catalog (regions, plans, offers, distros).
- State module `noderings`: `ssh_key_present`/`absent`, `dns_zone_present`/`absent`, `dns_record_present`/`absent`, `firewall_ruleset_present`/`absent`, `vm_present`/`absent`, all with `test=True` support.
- Salt Cloud driver `noderings`: order servers by plan code and city, bootstrap a minion over SSH, list, start, stop, reboot and destroy them.
