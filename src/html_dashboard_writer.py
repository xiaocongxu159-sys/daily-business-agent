# -*- coding: utf-8 -*-
"""Generate a dependency-free local HTML dashboard from the current workbook."""
from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd


def _clean(value: Any):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def _records(frame: pd.DataFrame) -> list[dict]:
    if frame is None or frame.empty:
        return []
    return [
        {str(column): _clean(value) for column, value in row.items()}
        for row in frame.to_dict("records")
    ]


def filter_dashboard_records(
    records,
    shop_id="",
    marketplace="",
    parent_asin="",
    asin="",
    offer_identity="",
):
    return [
        row
        for row in records
        if (not shop_id or str(row.get("shop_id", "")) == str(shop_id))
        and (not marketplace or str(row.get("marketplace", "")) == str(marketplace))
        and (not parent_asin or str(row.get("parent_asin", row.get("父ASIN", ""))) == str(parent_asin))
        and (not asin or str(row.get("asin", row.get("ASIN", ""))) == str(asin))
        and (not offer_identity or str(row.get("offer_identity", row.get("SKU", ""))) == str(offer_identity))
    ]


def _payload(excel_path: Path) -> dict:
    sheets = pd.read_excel(excel_path, sheet_name=None)
    daily = sheets.get("每日数据录入", pd.DataFrame())
    quality = sheets.get("数据质量报告", pd.DataFrame())
    return {
        "generated_from": excel_path.name,
        "daily": _records(daily),
        "quality": _records(quality),
    }


def _safe_json(value) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def write_html_dashboard(excel_path, output_dir=None):
    excel = Path(excel_path).resolve()
    output_root = Path(output_dir or excel.parent).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    payload = _payload(excel)
    json_path = output_root / "dashboard_data.json"
    html_path = output_root / "dashboard.html"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    encoded = _safe_json(payload)
    html = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; object-src 'none'; base-uri 'none'; form-action 'none'">
<title>每日经营看板</title><style>
body{margin:0;font-family:Inter,'Microsoft YaHei',sans-serif;background:#f4f6f9;color:#172033}.wrap{width:min(1280px,calc(100% - 32px));margin:24px auto}.notice,.card{background:#fff;border:1px solid #dde4ee;border-radius:12px;padding:16px;margin-bottom:14px}.notice{background:#ecfdf5;border-color:#9ee7c1}.filters{display:flex;gap:10px;flex-wrap:wrap}select,input{min-height:38px;padding:7px 9px;border:1px solid #cbd5e1;border-radius:7px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}.metric{background:#fff;border:1px solid #dde4ee;border-radius:10px;padding:14px}.metric b{display:block;font-size:24px;margin-top:5px}table{width:100%;border-collapse:collapse;font-size:13px}th,td{border-bottom:1px solid #e5e7eb;padding:8px;text-align:left;white-space:nowrap}th{position:sticky;top:0;background:#eff6ff}.table-wrap{max-height:62vh;overflow:auto}.muted{color:#64748b}@media(max-width:700px){.wrap{width:calc(100% - 18px)}}
</style></head><body><div class="wrap"><h1>每日经营看板</h1><div class="notice">全部数据来自本机当前分析任务。此页面不加载任何外部脚本或网络资源。</div>
<div class="card filters"><label>店铺 <select id="store"></select></label><label>站点 <select id="market"></select></label><label>搜索 <input id="search" placeholder="SKU / ASIN / 商品名称"></label></div>
<div id="cards" class="cards"></div><div class="card"><h2>每日商品数据</h2><div id="count" class="muted"></div><div class="table-wrap"><table><thead id="head"></thead><tbody id="body"></tbody></table></div></div>
<div class="card"><h2>数据质量</h2><div id="quality"></div></div></div>
<script type="application/json" id="dashboard-data">__DATA__</script><script>
const payload=JSON.parse(document.getElementById('dashboard-data').textContent);const rows=payload.daily||[];const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number=(row,names)=>{for(const n of names){const v=Number(row[n]);if(Number.isFinite(v))return v}return 0};const unique=(name)=>[...new Set(rows.map(r=>String(r[name]??'')).filter(Boolean))].sort();
function options(id,values,label){document.getElementById(id).innerHTML='<option value="">全部'+label+'</option>'+values.map(v=>`<option>${esc(v)}</option>`).join('')};options('store',unique('shop_id'),'店铺');options('market',unique('marketplace'),'站点');
const cols=['日期','shop_name','marketplace','SKU','ASIN','产品名称','Sessions','PV','总订单','销售额','广告花费','广告销售额','FBA可售库存','总库存'];
function render(){const store=document.getElementById('store').value,market=document.getElementById('market').value,q=document.getElementById('search').value.trim().toLowerCase();const filtered=rows.filter(r=>(!store||String(r.shop_id??'')===store)&&(!market||String(r.marketplace??'')===market)&&(!q||[r.SKU,r.ASIN,r['产品名称']].some(v=>String(v??'').toLowerCase().includes(q))));const sum=names=>filtered.reduce((a,r)=>a+number(r,names),0);const metrics=[['记录数',filtered.length],['销售额',sum(['销售额']).toFixed(2)],['订单量',sum(['总订单','Units Ordered']).toFixed(0)],['广告花费',sum(['广告花费']).toFixed(2)],['广告销售额',sum(['广告销售额']).toFixed(2)]];document.getElementById('cards').innerHTML=metrics.map(m=>`<div class="metric">${esc(m[0])}<b>${esc(m[1])}</b></div>`).join('');document.getElementById('count').textContent=`显示 ${filtered.length} 条记录`;document.getElementById('head').innerHTML='<tr>'+cols.map(c=>`<th>${esc(c)}</th>`).join('')+'</tr>';document.getElementById('body').innerHTML=filtered.slice(0,2000).map(r=>'<tr>'+cols.map(c=>`<td>${esc(r[c])}</td>`).join('')+'</tr>').join('')}
['store','market','search'].forEach(id=>document.getElementById(id).addEventListener(id==='search'?'input':'change',render));document.getElementById('quality').innerHTML=(payload.quality||[]).map(r=>`<div><b>${esc(r['类别'])}</b>：${esc(r['内容'])}</div>`).join('')||'<span class="muted">未发现数据质量问题</span>';render();
</script></body></html>""".replace("__DATA__", encoded)
    html_path.write_text(html, encoding="utf-8")
    return html_path, json_path
