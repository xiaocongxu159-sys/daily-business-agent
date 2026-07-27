# -*- coding: utf-8 -*-
"""Static UX contract for the single-file Lingxing setup page."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "agent" / "static" / "lingxing.html"


def page_text() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_page_uses_single_file_beginner_flow() -> None:
    html = page_text()
    assert "单文件私人连接包" in html
    assert "双击服务器生成的" in html
    assert "测试连接、安全保存并删除连接包" in html
    assert 'id="package-ready"' in html


def test_page_hides_technical_connection_fields() -> None:
    html = page_text()
    assert 'id="proxy-url"' not in html
    assert 'type="file"' not in html
    assert "证书指纹和内部完整性信息不会显示" in html
    assert "tls+http://用户名" not in html


def test_source_deletion_is_only_claimed_after_success() -> None:
    html = page_text()
    assert "连接成功后将自动删除原文件" in html
    assert "Windows 未能删除原始 .dba 文件" in html
    assert "请手动删除该文件" in html
