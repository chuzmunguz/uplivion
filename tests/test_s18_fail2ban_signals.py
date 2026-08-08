import re
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Fail2banSignalTest(unittest.TestCase):
    def test_only_rejected_login_matches_authentication_signal(self):
        regex = re.compile(
            r'^(?P<host>\S+) .*"POST /login(?:\?[^ ]*)? HTTP/[0-9.]+" 401 .+$'
        )
        rejected_login = (
            '203.0.113.5 - - [26/Jul/2026:12:00:00 +0000] '
            '"POST /login HTTP/1.1" 401 42 "-" "browser"'
        )
        self.assertRegex(rejected_login, regex)

        legitimate_or_unrelated = (
            '10.9.0.2 - - [26/Jul/2026:12:00:00 +0000] '
            '"GET /links HTTP/1.1" 403 42 "-" "browser"',
            '203.0.113.5 - - [26/Jul/2026:12:00:00 +0000] '
            '"POST /session HTTP/1.1" 401 42 "-" "browser"',
            '203.0.113.5 - - [26/Jul/2026:12:00:00 +0000] '
            '"GET /missing.png HTTP/1.1" 404 42 "-" "browser"',
            '203.0.113.5 - - [26/Jul/2026:12:00:00 +0000] '
            '"GET /?utm=x HTTP/1.1" 200 42 "-" "browser"',
        )
        for line in legitimate_or_unrelated:
            with self.subTest(line=line):
                self.assertNotRegex(line, regex)

    def test_all_jails_are_enabled(self):
        jail = (ROOT / "fail2ban" / "uplivion-jail.local").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("enabled = false", jail)
        self.assertEqual(jail.count("enabled = true"), 4)
        for name in (
            "uplivion-http-method",
            "uplivion-unauth-notfound",
            "uplivion-auth-failure",
            "uplivion-rate-limit",
        ):
            with self.subTest(name=name):
                self.assertIn(f"[{name}]", jail)

    def test_http_method_whitelist_exempts_only_the_public_share_surface(self):
        # /share is the only route a non-owner is ever meant to reach; every
        # other route lives on the LAN/WireGuard-gated private vhost, where
        # legitimate access is covered by ignoreip, not this whitelist. If a
        # private-only route prefix ever leaks into the whitelist regex,
        # that's this filter silently widening past its intended public
        # surface. Checked against the failregex itself, not the file's
        # prose comments, which name those routes for explanatory purposes.
        method_filter = (
            ROOT / "fail2ban" / "filters" / "uplivion-http-method.local"
        ).read_text(encoding="utf-8")
        path_whitelist_line = re.search(
            r"^failregex = (.+)$", method_filter, re.MULTILINE
        ).group(1)

        server = (ROOT / "server.py").read_text(encoding="utf-8")
        route_prefixes = sorted(
            {
                path.split("/")[1]
                for path in re.findall(r'@app\.route\("(/[^"]+)"', server)
            }
        )
        self.assertIn("share", route_prefixes)
        for prefix in route_prefixes:
            if prefix == "share":
                continue
            with self.subTest(prefix=prefix):
                self.assertNotIn(prefix, path_whitelist_line)

    def test_http_method_whitelist_covers_real_ios_link_preview_probes(self):
        # Regression test for a real production log line: iOS's link-preview
        # prefetcher (UA "NetworkingExtension/...") requests favicon.ico,
        # apple-touch-icon.png, AND apple-touch-icon-precomposed.png as a set
        # when a share link arrives via Messages/Mail. Missing the
        # -precomposed sibling once meant a recipient's own device would
        # single-strike ban itself before they ever tapped the link.
        method_filter = (
            ROOT / "fail2ban" / "filters" / "uplivion-http-method.local"
        ).read_text(encoding="utf-8")
        m = re.search(r"failregex = (.+?)\n\n", method_filter, re.S)
        fail_lines = [l.strip() for l in m.group(1).splitlines() if l.strip()]
        fail_res = [re.compile(re.sub(r"<HOST>", r"\\S+", p)) for p in fail_lines]

        real_ios_probes = (
            '176.78.222.123 - - [09/Jun/2026:14:31:05 +0100] '
            '"GET /apple-touch-icon-precomposed.png HTTP/1.1" 444 0 "-" '
            '"NetworkingExtension/8623.1.14.10.9 Network/5569.60.39.0.3 iOS/26.2"',
            '176.78.222.123 - - [09/Jun/2026:14:31:05 +0100] '
            '"GET /favicon.ico HTTP/1.1" 444 0 "-" '
            '"NetworkingExtension/8623.1.14.10.9 Network/5569.60.39.0.3 iOS/26.2"',
            '176.78.222.123 - - [09/Jun/2026:14:31:05 +0100] '
            '"GET /apple-touch-icon.png HTTP/1.1" 444 0 "-" '
            '"NetworkingExtension/8623.1.14.10.9 Network/5569.60.39.0.3 iOS/26.2"',
        )
        for line in real_ios_probes:
            with self.subTest(line=line):
                self.assertFalse(any(r.match(line) for r in fail_res))

