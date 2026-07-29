# -*- coding: utf-8 -*-
"""Final local-dashboard validation and Lingxing operator-view cleanup."""
from __future__ import annotations

import html
import json
import os
import re
import tempfile
from pathlib import Path

REMOTE_RESOURCE = re.compile(r"(?:src|href)=[\"']https?://", re.IGNORECASE)
DATA_SCRIPT = re.compile(
    r'(<script type="application/json" id="dashboard-data">)(.*?)(</script>)',
    re.DOTALL,
)
DETAIL_SECTIONS = re.compile(
    r'\s*<section class="panel"><h2>每日商品数据</h2>.*?</section>'
    r'\s*<section class="panel"><h2>数据质量</h2>.*?</section>',
    re.DOTALL,
)
STORE_SETUP_OLD = (
    "const unique=name=>[...new Set(rows.map(r=>String(r[name]??'')).filter(Boolean))].sort();\n"
    "  function options(id,values,label){document.getElementById(id).innerHTML='<option value=\"\">全部'+label+'</option>'+values.map(v=>`<option value=\"${esc(v)}\">${esc(v)}</option>`).join('')}\n"
    "  options('store',unique('shop_id'),'店铺');options('market',unique('marketplace'),'站点');"
)
STORE_SETUP_NEW = (
    "const unique=name=>[...new Set(rows.map(r=>String(r[name]??'')).filter(Boolean))].sort();\n"
    "  function options(id,values,label){document.getElementById(id).innerHTML='<option value=\"\">全部'+label+'</option>'+values.map(v=>`<option value=\"${esc(v)}\">${esc(v)}</option>`).join('')}\n"
    "  function storeOptions(){const names=new Map();for(const row of rows){const id=String(row.shop_id??'').trim();if(!id)continue;const name=String(row.shop_name??'').trim()||id;if(!names.has(id)||names.get(id)===id)names.set(id,name)}const values=[...names.entries()].sort((a,b)=>a[1].localeCompare(b[1],'zh-CN'));document.getElementById('store').innerHTML='<option value=\"\">全部店铺</option>'+values.map(([id,name])=>`<option value=\"${esc(id)}\">${esc(name)}</option>`).join('')}\n"
    "  storeOptions();options('market',unique('marketplace'),'站点');"
)
SUMMARY_OLD = (
    "filterSummary.textContent=`${store.value||'全部店铺'} · ${market.value||'全部站点'} · "
    "${search.value||'全部商品'} · ${startDate.value||'最早'} 至 ${endDate.value||'最新'}`"
)
SUMMARY_NEW = (
    "const selectedStoreLabel=store.value?(store.options[store.selectedIndex]?.textContent||store.value):'全部店铺';"
    "filterSummary.textContent=`${selectedStoreLabel} · ${market.value||'全部站点'} · "
    "${search.value||'全部商品'} · ${startDate.value||'最早'} 至 ${endDate.value||'最新'}`"
)
METRIC_OLD = "const metrics=[['记录数',data.length,'筛选后商品数据行'],"
METRIC_NEW = (
    "const productCount=new Set(data.map(r=>`${r.shop_id??''}|${r.offer_identity??r.SKU??r.MSKU??r.ASIN??''}`)"
    ".filter(v=>!v.endsWith('|'))).size;const metrics=[['商品数',productCount,'筛选范围内去重'],"
)
DETAIL_RENDER_START = ";const cols=['日期','shop_name','marketplace','SKU','ASIN','产品名称','Sessions','PV','总订单','销售额','广告花费','广告销售额','FBA可售库存','总库存'];"
DETAIL_RENDER_END = "||'<span class=\"muted\">未发现数据质量问题</span>'"


def _safe_json(value) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _clean_payload_and_notice(text: str) -> tuple[str, str, bool]:
    match = DATA_SCRIPT.search(text)
    if match is None:
        raise ValueError("dashboard data payload is missing")
    try:
        payload = json.loads(match.group(2))
    except json.JSONDecodeError as exc:
        raise ValueError("dashboard data payload is invalid") from exc

    source_mode = str((payload.get("meta") or {}).get("source_mode") or "").strip()
    if source_mode != "lingxing_local_sync":
        return text, "", False

    # Technical quality rows stay in the local workbook/task JSON, not in the
    # operator-facing Lingxing HTML document.
    payload["quality"] = []
    supplemental = payload.get("supplemental_daily") or []
    source_rows = 0
    for row in supplemental:
        if not isinstance(row, dict):
            continue
        try:
            source_rows += max(0, int(float(row.get("sourceRows") or 0)))
        except (TypeError, ValueError):
            continue

    parts = []
    inventory_day = str((payload.get("meta") or {}).get("inventory_snapshot_date") or "").strip()
    if inventory_day:
        parts.append(f"库存快照日期：{inventory_day}")
    if source_rows:
        parts.append(f"{source_rows} 条店铺级广告汇总已计入广告总额，未分摊到具体商品")
    notice = ""
    if parts:
        notice = (
            '  <div class="notice" data-operator-summary="1"><b>运营提示：</b>'
            + html.escape("；".join(parts) + "。", quote=True)
            + "</div>\n"
        )

    replacement = match.group(1) + _safe_json(payload) + match.group(3)
    return text[: match.start()] + replacement + text[match.end() :], notice, True


def _remove_detail_rendering(text: str) -> str:
    start = text.find(DETAIL_RENDER_START)
    if start < 0:
        raise ValueError("dashboard detail rendering marker is missing")
    end = text.find(DETAIL_RENDER_END, start)
    if end < 0:
        raise ValueError("dashboard quality rendering marker is missing")
    return text[:start] + text[end + len(DETAIL_RENDER_END) :]


def patch_dashboard_html_file(path):
    target = Path(path).resolve()
    text = target.read_text(encoding="utf-8")
    if REMOTE_RESOURCE.search(text):
        raise ValueError("dashboard contains a remote script or stylesheet")
    if "每日经营看板" not in text:
        raise ValueError("dashboard title is missing")

    text, notice, operator_view = _clean_payload_and_notice(text)
    if operator_view:
        text, count = DETAIL_SECTIONS.subn("\n" + notice, text, count=1)
        if count != 1:
            raise ValueError("dashboard operator detail sections are missing")
        if STORE_SETUP_OLD not in text:
            raise ValueError("dashboard store filter marker is missing")
        text = text.replace(STORE_SETUP_OLD, STORE_SETUP_NEW, 1)
        if SUMMARY_OLD not in text:
            raise ValueError("dashboard filter summary marker is missing")
        text = text.replace(SUMMARY_OLD, SUMMARY_NEW, 1)
        if METRIC_OLD not in text:
            raise ValueError("dashboard record metric marker is missing")
        text = text.replace(METRIC_OLD, METRIC_NEW, 1)
        text = _remove_detail_rendering(text)

    if "data-local-dashboard-checked" not in text:
        text = text.replace("<html ", '<html data-local-dashboard-checked="true" ', 1)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    return target
