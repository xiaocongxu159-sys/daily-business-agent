# -*- coding: utf-8 -*-
"""Static UX contract for beginner-safe Lingxing configuration."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "agent" / "static" / "lingxing.html"


def page_text() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_admin_network_settings_are_nested_and_closed_by_default() -> None:
    html = page_text()
    assert '<details id="admin-settings"' in html
    assert '<details id="admin-settings" open' not in html
    assert "管理员高级设置（普通用户无需填写）" in html


def test_page_explains_beginner_boundary() -> None:
    html = page_text()
    assert "普通用户只需要准备自己的领星 AppID 和 AppSecret" in html
    assert "请联系企业管理员" in html
    assert "无法从领星后台或 Windows 设置中找到" in html
    assert "未配置领星也不影响手工报表分析" in html


def test_missing_admin_egress_is_blocked_before_api_submission() -> None:
    html = page_text()
    assert 'if(!proxyUrl)' in html
    assert '$("admin-settings").open=true' in html
    assert "尚未配置企业管理员固定出口" in html
    assert 'proxy_url:proxyUrl' in html


def test_advanced_field_does_not_claim_to_be_lingxing_credentials() -> None:
    html = page_text()
    assert "这不是领星账号信息" in html
    assert "普通用户不要自行拼接" in html
    assert "tls+http://用户名:密码@服务器:端口?sha256=64位证书指纹" in html
