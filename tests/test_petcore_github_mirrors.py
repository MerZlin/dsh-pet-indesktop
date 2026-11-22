"""GitHub 公益镜像与自定义前缀单一真值单元测试。"""

from __future__ import annotations

import pytest

from petcore.net.github_mirrors import (
    ALL_MIRROR_POLICIES,
    CUSTOM_MIRROR_POLICY,
    GITHUB_PROXY_MIRRORS,
    MIRROR_POLICY_OPTIONS,
    PROXY_AUTO_POLICY,
    expand_github_urls,
    is_proxy_policy,
    normalize_custom_prefix,
    normalize_policy,
    proxy_hosts_for_policy,
    wrap_github_url,
)


def test_policy_constants_and_options_match() -> None:
    assert ALL_MIRROR_POLICIES == (
        "auto",
        "gh-proxy",
        "ghfast",
        "ghproxy-net",
        "ghproxy-cc",
        "ghproxy-auto",
        "custom",
        "mirror",
        "github",
    )
    option_values = tuple(value for _label, value in MIRROR_POLICY_OPTIONS)
    assert option_values == ALL_MIRROR_POLICIES
    assert [m.host for m in GITHUB_PROXY_MIRRORS] == [
        "gh-proxy.com",
        "ghfast.top",
        "ghproxy.net",
        "ghproxy.cc",
    ]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "auto"),
        ("", "auto"),
        ("  GH-PROXY  ", "gh-proxy"),
        ("GHFAST", "ghfast"),
        ("ghproxy-net", "ghproxy-net"),
        ("ghproxy-cc", "ghproxy-cc"),
        ("GHPROXY-AUTO", "ghproxy-auto"),
        ("CUSTOM", "custom"),
        ("MIRROR", "mirror"),
        ("github", "github"),
        ("jsdelivr", "auto"),
        ("unknown", "auto"),
    ],
)
def test_normalize_policy(raw, expected: str) -> None:
    assert normalize_policy(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://gh-proxy.com", "https://gh-proxy.com/"),
        ("https://GH-PROXY.COM/", "https://gh-proxy.com/"),
        ("  https://mirror.example.com/gh/  ", "https://mirror.example.com/gh/"),
        ("https://mirror.example.com:8443/sub", "https://mirror.example.com:8443/sub/"),
        ("https://[::1]:8443/proxy", "https://[::1]:8443/proxy/"),
        ("", ""),
        (None, ""),
        ("http://gh-proxy.com/", ""),
        ("ftp://gh-proxy.com/", ""),
        ("https://", ""),
        ("https://user:pass@gh-proxy.com/", ""),
        ("https://user@gh-proxy.com/", ""),
        ("https://gh-proxy.com/?a=1", ""),
        ("https://gh-proxy.com/?", ""),
        ("https://gh-proxy.com/#frag", ""),
        ("https://gh-proxy.com/bad space", ""),
        ("https://gh-proxy.com/\n", "https://gh-proxy.com/"),
        ("https://gh-proxy.com/bad\nline", ""),
        ("https://gh-proxy.com/../evil", ""),
        ("https://gh-proxy.com:99999/", ""),
        ("https://example.com/" + ("a" * 500), ""),
    ],
)
def test_normalize_custom_prefix(raw, expected: str) -> None:
    assert normalize_custom_prefix(raw) == expected


def test_wrap_github_url_accepts_only_safe_download_paths() -> None:
    mirror = GITHUB_PROXY_MIRRORS[0]
    release_url = "https://github.com/MerZlin/Animations/releases/download/packs-v1/shenshen-1.zip"
    raw_url = "https://raw.githubusercontent.com/hunmer/aboutme-pets/main/pets/dsh/manifest.json"
    archive_url = "https://github.com/MerZlin/dsh-pet-indesktop/archive/refs/tags/v4.3.4.zip"
    github_raw_url = "https://github.com/a/b/raw/main/file.json"

    assert wrap_github_url(release_url, mirror) == f"https://gh-proxy.com/{release_url}"
    assert wrap_github_url(raw_url, mirror) == f"https://gh-proxy.com/{raw_url}"
    assert wrap_github_url(archive_url, mirror) == f"https://gh-proxy.com/{archive_url}"
    assert wrap_github_url(github_raw_url, mirror) == f"https://gh-proxy.com/{github_raw_url}"

    # 绝不包装 API、签名 CDN、非 HTTPS、带凭据、非下载页面或已代理链接。
    rejected = [
        "https://api.github.com/repos/MerZlin/Animations/releases/tags/packs-v1",
        "https://release-assets.githubusercontent.com/abc/file.zip?sig=1",
        "https://objects.githubusercontent.com/github-production-release-asset/1",
        "https://github-releases.githubusercontent.com/123",
        "https://cdn.jsdelivr.net/gh/MerZlin/Animations@main/packs-index.json",
        "https://github.com/MerZlin/Animations",
        "https://raw.githubusercontent.com/",
        "http://github.com/MerZlin/Animations/releases/download/packs-v1/a.zip",
        "https://user:pass@github.com/MerZlin/Animations/releases/download/packs-v1/a.zip",
        f"https://gh-proxy.com/{release_url}",
    ]
    for url in rejected:
        assert wrap_github_url(url, mirror) is None, url


def test_expand_github_urls_and_proxy_hosts() -> None:
    release_url = "https://github.com/MerZlin/Animations/releases/download/packs-v1/shenshen-1.zip"
    jsdelivr_url = "https://fastly.jsdelivr.net/gh/MerZlin/Animations@main/packs-index.json"

    for base in ("auto", "mirror", "github"):
        assert is_proxy_policy(base) is False
        assert proxy_hosts_for_policy(base, custom_prefix="https://custom.example.com/") == ()
        assert expand_github_urls(
            (release_url, jsdelivr_url),
            base,
            custom_prefix="https://custom.example.com/",
        ) == (release_url, jsdelivr_url)

    assert is_proxy_policy("gh-proxy") is True
    assert proxy_hosts_for_policy("gh-proxy") == ("gh-proxy.com",)
    assert expand_github_urls((release_url, jsdelivr_url), "gh-proxy") == (
        f"https://gh-proxy.com/{release_url}",
        release_url,
        jsdelivr_url,
    )

    assert is_proxy_policy(PROXY_AUTO_POLICY) is True
    assert proxy_hosts_for_policy(PROXY_AUTO_POLICY) == (
        "gh-proxy.com",
        "ghfast.top",
        "ghproxy.net",
        "ghproxy.cc",
    )
    assert expand_github_urls((release_url,), PROXY_AUTO_POLICY) == (
        f"https://gh-proxy.com/{release_url}",
        f"https://ghfast.top/{release_url}",
        f"https://ghproxy.net/{release_url}",
        f"https://ghproxy.cc/{release_url}",
        release_url,
    )

    assert is_proxy_policy(CUSTOM_MIRROR_POLICY) is True
    assert proxy_hosts_for_policy(CUSTOM_MIRROR_POLICY, custom_prefix="https://my.mirror/p") == (
        "my.mirror",
    )
    assert expand_github_urls(
        (release_url,), CUSTOM_MIRROR_POLICY, custom_prefix="https://my.mirror/p"
    ) == (
        f"https://my.mirror/p/{release_url}",
        release_url,
    )
    assert proxy_hosts_for_policy(CUSTOM_MIRROR_POLICY, custom_prefix="http://bad") == ()
    assert expand_github_urls(
        (release_url,), CUSTOM_MIRROR_POLICY, custom_prefix=""
    ) == (release_url,)
