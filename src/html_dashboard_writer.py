# -*- coding: utf-8 -*-
"""Generate a rich, dependency-free local HTML dashboard.

The dashboard is fully self-contained: it embeds the current job payload and
uses inline SVG for charts. It never loads remote scripts, fonts or styles.
"""
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
        and (
            not parent_asin
            or str(row.get("parent_asin", row.get("父ASIN", "")))
            == str(parent_asin)
        )
        and (not asin or str(row.get("asin", row.get("ASIN", ""))) == str(asin))
        and (
            not offer_identity
            or str(row.get("offer_identity", row.get("SKU", "")))
            == str(offer_identity)
        )
    ]


def _metric_status(availability: dict, key: str) -> str:
    value = availability.get(key, "")
    if isinstance(value, dict):
        return str(value.get("status") or "")
    return str(value or "")


def _payload(excel_path: Path, dashboard_context: dict | None = None) -> dict:
    sheets = pd.read_excel(excel_path, sheet_name=None)
    daily = sheets.get("每日数据录入", pd.DataFrame())
    quality = sheets.get("数据质量报告", pd.DataFrame())
    dates = pd.to_datetime(
        daily.get("日期", pd.Series(dtype=object)), errors="coerce"
    ).dropna()
    context = dict(dashboard_context or {})
    availability = dict(context.get("metric_availability") or {})
    daily_records = _records(daily)
    if _metric_status(availability, "sessions") == "unavailable":
        for row in daily_records:
            row["Sessions"] = None
    if _metric_status(availability, "page_views") == "unavailable":
        for row in daily_records:
            row["PV"] = None
            row["Page Views"] = None

    meta = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "generated_from": excel_path.name,
        "min_date": dates.min().strftime("%Y-%m-%d") if not dates.empty else "",
        "max_date": dates.max().strftime("%Y-%m-%d") if not dates.empty else "",
    }
    meta.update(dict(context.get("meta") or {}))
    quality_records = _records(quality)
    for item in context.get("quality_notes") or []:
        if isinstance(item, dict):
            quality_records.append(
                {str(key): _clean(value) for key, value in item.items()}
            )

    return {
        "meta": meta,
        "generated_from": excel_path.name,
        "daily": daily_records,
        "quality": quality_records,
        "metric_availability": availability,
        "supplemental_daily": list(context.get("supplemental_daily") or []),
    }


def _safe_json(value) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


HTML_TEMPLATE = r'''<!doctype html>
<html lang="zh-CN" data-local-dashboard-checked="true">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; object-src 'none'; base-uri 'none'; form-action 'none'">
  <title>每日经营看板</title>
  <style>
    :root{--ink:#17243a;--muted:#64748b;--line:#dce4ef;--panel:#fff;--bg:#f4f7fb;--blue:#2878b5;--green:#2a9d6f;--amber:#d79b22;--red:#c85555;--purple:#7656a8;--shadow:0 4px 18px rgba(30,48,76,.06)}
    *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}.shell{width:min(100% - 32px,1440px);margin:0 auto;padding-bottom:32px}
    header{display:flex;justify-content:space-between;gap:24px;align-items:flex-end;padding:25px 0 17px}h1{margin:0;font-size:28px;color:#1d3557}.subtitle{margin:8px 0 0;color:var(--muted)}.source{font-size:12px;line-height:1.7;color:var(--muted);text-align:right}
    .notice,.panel,.metric,.chart-card{background:var(--panel);border:1px solid var(--line);border-radius:11px;box-shadow:var(--shadow)}.notice{padding:14px 16px;margin-bottom:14px;background:#ecfdf5;border-color:#9ee7c1}.notice.warning{background:#fffbeb;border-color:#f2c66d;color:#7c4a03}
    details.filters{margin-bottom:14px}.filters>summary{list-style:none;display:flex;align-items:center;gap:12px;min-height:50px;padding:0 16px;cursor:pointer}.filters>summary::-webkit-details-marker{display:none}.filters>summary b{color:#24405f}.filters>summary span{flex:1;min-width:0;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.filters>summary em{font-style:normal;color:#315b86;font-weight:700}
    .controls{display:grid;grid-template-columns:repeat(6,minmax(125px,1fr));gap:11px;padding:0 16px 16px}.controls label{display:flex;flex-direction:column;gap:6px;color:var(--muted);font-size:12px;font-weight:600}.controls input,.controls select{height:38px;border:1px solid #cbd5e1;border-radius:7px;background:#fff;padding:0 9px;color:var(--ink)}
    .metrics{display:grid;grid-template-columns:repeat(8,minmax(0,1fr));gap:10px;margin-bottom:14px}.metric{padding:13px 14px;min-width:0}.metric span{display:block;color:var(--muted);font-size:12px}.metric strong{display:block;margin-top:7px;font-size:21px;color:#203a5d;overflow:hidden;text-overflow:ellipsis}.metric small{display:block;margin-top:5px;color:#94a3b8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .charts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-bottom:14px}.chart-card{padding:10px 12px 12px;overflow:hidden}.chart{position:relative;height:340px;width:100%;touch-action:pan-y}.chart svg{display:block;width:100%;height:100%}.chart-empty{height:100%;display:flex;align-items:center;justify-content:center;color:var(--muted);text-align:center;padding:24px}
    .chart-tooltip{position:absolute;z-index:5;min-width:132px;max-width:220px;padding:9px 11px;border-radius:6px;background:rgba(31,38,48,.95);color:#fff;box-shadow:0 5px 18px rgba(15,23,42,.3);font-size:12px;line-height:1.55;pointer-events:none;white-space:nowrap}.chart-tooltip[hidden]{display:none}.chart-tooltip b{display:block;margin-bottom:4px;font-size:12px}.chart-tooltip-row{display:grid;grid-template-columns:9px minmax(0,1fr) auto;align-items:center;gap:6px}.chart-tooltip-dot{width:7px;height:7px;border-radius:50%}.chart-tooltip-row strong{font-weight:700;text-align:right}
    .panel{padding:16px;margin-bottom:14px}.panel h2{margin:0 0 12px;font-size:18px;color:#243e60}.muted{color:var(--muted)}.table-wrap{max-height:55vh;overflow:auto}table{width:100%;border-collapse:collapse;font-size:12px}th,td{border-bottom:1px solid #e7edf4;padding:8px;text-align:left;white-space:nowrap}th{position:sticky;top:0;background:#eff6ff;z-index:1}.quality-row{padding:7px 0;border-bottom:1px solid #edf1f5}.quality-row:last-child{border-bottom:0}
    @media(max-width:1180px){.metrics{grid-template-columns:repeat(4,1fr)}.controls{grid-template-columns:repeat(3,1fr)}}@media(max-width:820px){header{align-items:flex-start;flex-direction:column}.source{text-align:left}.charts{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(2,1fr)}.controls{grid-template-columns:repeat(2,1fr)}}@media(max-width:520px){.shell{width:calc(100% - 18px)}.controls,.metrics{grid-template-columns:1fr}.chart{height:320px}}
  </style>
</head>
<body><div class="shell">
  <header><div><h1>每日经营动态看板</h1><p class="subtitle">业务流量、销售、广告和库存趋势均来自本机当前分析任务</p></div><div id="source" class="source"></div></header>
  <div class="notice">全部数据只在本机处理。页面不加载任何外部脚本、字体或网络资源。</div>
  <div id="availabilityNotice" class="notice warning" hidden></div>
  <details class="filters panel" id="filterDetails"><summary><b>筛选条件</b><span id="filterSummary">全部店铺 · 全部站点 · 全部商品</span><em id="filterToggle">展开筛选</em></summary><div class="controls">
    <label>店铺<select id="store"></select></label><label>站点<select id="market"></select></label><label>开始日期<input type="date" id="startDate"></label><label>结束日期<input type="date" id="endDate"></label><label style="grid-column:span 2">搜索<input id="search" placeholder="SKU / ASIN / 商品名称"></label>
  </div></details>
  <div id="cards" class="metrics"></div>
  <section class="charts"><div class="chart-card"><div id="trafficChart" class="chart"></div></div><div class="chart-card"><div id="salesChart" class="chart"></div></div><div class="chart-card"><div id="adsChart" class="chart"></div></div><div class="chart-card"><div id="inventoryChart" class="chart"></div></div></section>
  <section class="panel"><h2>每日商品数据</h2><div id="count" class="muted"></div><div class="table-wrap"><table><thead id="head"></thead><tbody id="body"></tbody></table></div></section>
  <section class="panel"><h2>数据质量</h2><div id="quality"></div></section>
</div>
<script type="application/json" id="dashboard-data">__DATA__</script>
<script>
(() => {
  'use strict';
  const payload=JSON.parse(document.getElementById('dashboard-data').textContent),rows=payload.daily||[],supplemental=payload.supplemental_daily||[],availability=payload.metric_availability||{};
  const metricStatus=key=>typeof availability[key]==='object'?String(availability[key]?.status||''):String(availability[key]||''),metricReason=key=>typeof availability[key]==='object'?String(availability[key]?.reason||''):'';
  const sessionsAvailable=metricStatus('sessions')!=='unavailable',pageViewsAvailable=metricStatus('page_views')!=='unavailable',trafficAvailable=sessionsAvailable&&pageViewsAvailable;
  const colors=['#2878b5','#2a9d6f','#d79b22','#c85555','#7656a8'];const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=(row,names)=>{for(const n of names){const raw=row[n];if(raw===null||raw===undefined||raw==='')continue;const v=Number(raw);if(Number.isFinite(v))return v}return 0};const txt=(row,names)=>{for(const n of names){const v=row[n];if(v!==undefined&&v!==null&&String(v)!=='')return String(v)}return''};const dateOf=r=>txt(r,['日期','date','report_date']).slice(0,10);
  const unique=name=>[...new Set(rows.map(r=>String(r[name]??'')).filter(Boolean))].sort();
  function options(id,values,label){document.getElementById(id).innerHTML='<option value="">全部'+label+'</option>'+values.map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join('')}
  options('store',unique('shop_id'),'店铺');options('market',unique('marketplace'),'站点');
  const dates=rows.map(dateOf).filter(Boolean).sort();if(dates.length){startDate.value=dates[0];endDate.value=dates[dates.length-1];startDate.min=endDate.min=dates[0];startDate.max=endDate.max=dates[dates.length-1]}
  const sourceLabel=payload.meta?.source_label||payload.generated_from||payload.meta?.generated_from||'-';source.innerHTML=`生成时间：${esc(payload.meta?.generated_at||'-')}<br>数据范围：${esc(payload.meta?.min_date||'-')} 至 ${esc(payload.meta?.max_date||'-')}<br>来源：${esc(sourceLabel)}`;
  const unavailable=[];if(!sessionsAvailable)unavailable.push('Sessions');if(!pageViewsAvailable)unavailable.push('PV');if(unavailable.length){availabilityNotice.hidden=false;availabilityNotice.textContent=`${unavailable.join(' / ')} 暂不可用：${metricReason('sessions')||metricReason('page_views')||'当前数据源未提供'}。缺失值不会显示为 0。`}
  filterDetails.addEventListener('toggle',()=>filterToggle.textContent=filterDetails.open?'收起筛选':'展开筛选');
  function filteredRows(){const s=store.value,m=market.value,q=search.value.trim().toLowerCase(),a=startDate.value,b=endDate.value;return rows.filter(r=>{const d=dateOf(r);return(!s||String(r.shop_id??'')===s)&&(!m||String(r.marketplace??'')===m)&&(!a||d>=a)&&(!b||d<=b)&&(!q||[r.SKU,r.MSKU,r.ASIN,r['产品名称']].some(v=>String(v??'').toLowerCase().includes(q)))})}
  function filteredSupplemental(){const s=store.value,m=market.value,q=search.value.trim(),a=startDate.value,b=endDate.value;if(q)return[];return supplemental.filter(r=>{const d=dateOf(r);return(!s||String(r.shop_id??'')===s)&&(!m||String(r.marketplace??'')===m)&&(!a||d>=a)&&(!b||d<=b)})}
  function grouped(data,extra){const map=new Map();for(const r of data){const d=dateOf(r);if(!d)continue;const x=map.get(d)||{date:d,sessions:0,pv:0,orders:0,sales:0,adSpend:0,adSales:0,fba:0,inventory:0};x.sessions+=num(r,['Sessions']);x.pv+=num(r,['PV','Page Views']);x.orders+=num(r,['总订单','Units Ordered','订单量']);x.sales+=num(r,['销售额','Ordered Product Sales']);x.adSpend+=num(r,['广告花费','Spend']);x.adSales+=num(r,['广告销售额','Sales']);x.fba+=num(r,['FBA可售库存']);x.inventory+=num(r,['总库存']);map.set(d,x)}for(const r of extra){const d=dateOf(r);if(!d)continue;const x=map.get(d)||{date:d,sessions:0,pv:0,orders:0,sales:0,adSpend:0,adSales:0,fba:0,inventory:0};x.adSpend+=num(r,['adSpend','广告花费']);x.adSales+=num(r,['adSales','广告销售额']);map.set(d,x)}return[...map.values()].sort((a,b)=>a.date.localeCompare(b.date))}
  function uprightAxis(label,side,x,centerY){const chars=[...String(label||'')].filter(char=>!/^\s$/.test(char)),lineHeight=15,start=centerY-(chars.length-1)*lineHeight/2;return `<text data-axis-label="${side}" data-axis-upright="1" x="${x}" y="${centerY}" text-anchor="middle" dominant-baseline="middle" font-size="11" font-weight="600" fill="#475569">${chars.map((char,index)=>`<tspan x="${x}" y="${start+index*lineHeight}">${esc(char)}</tspan>`).join('')}</text>`}
  function displayValue(value){const number=Number(value)||0;return Number.isInteger(number)?number.toLocaleString('zh-CN'):number.toLocaleString('zh-CN',{maximumFractionDigits:2})}
  function chart(id,title,data,series,axisLabels={}){
    const box=document.getElementById(id),w=Math.max(360,box.clientWidth||720),h=Math.max(300,box.clientHeight||340);
    if(!data.length){box.innerHTML=`<div class="chart-empty">${esc(title)}：当前筛选条件无数据</div>`;return}
    const hasRight=series.some(s=>(s.axis||0)===1),leftLabel=axisLabels.left||'',rightLabel=axisLabels.right||'',L=leftLabel?72:58,R=hasRight&&rightLabel?72:34,T=72,B=54,pw=w-L-R,ph=h-T-B,axisMax=[0,0],midY=T+ph/2;
    series.forEach(s=>data.forEach(r=>axisMax[s.axis||0]=Math.max(axisMax[s.axis||0],Number(r[s.key])||0)));axisMax[0]=axisMax[0]||1;axisMax[1]=axisMax[1]||1;
    let out=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(title)}"><text x="16" y="27" font-size="16" font-weight="700" fill="#243e60">${esc(title)}</text>`;
    let lx=16;series.forEach((s,i)=>{out+=`<line x1="${lx}" y1="50" x2="${lx+18}" y2="50" stroke="${colors[i]}" stroke-width="3"/><text x="${lx+24}" y="54" font-size="11" fill="#475569">${esc(s.name)}</text>`;lx+=Math.max(92,48+s.name.length*12)});
    if(leftLabel)out+=uprightAxis(leftLabel,'left',18,midY);if(hasRight&&rightLabel)out+=uprightAxis(rightLabel,'right',w-18,midY);
    for(let i=0;i<=4;i++){const y=T+ph*i/4;out+=`<line x1="${L}" y1="${y}" x2="${L+pw}" y2="${y}" stroke="#e9eef5"/><text x="${L-8}" y="${y+4}" text-anchor="end" font-size="10" fill="#64748b">${(axisMax[0]*(4-i)/4).toFixed(axisMax[0]<20?1:0)}</text>`;if(hasRight)out+=`<text x="${L+pw+8}" y="${y+4}" font-size="10" fill="#64748b">${(axisMax[1]*(4-i)/4).toFixed(axisMax[1]<20?1:0)}</text>`}
    const step=data.length>1?pw/(data.length-1):0,skip=Math.max(1,Math.ceil(data.length/8));data.forEach((r,i)=>{if(i%skip===0||i===data.length-1){const x=L+(data.length>1?i*step:pw/2);out+=`<text x="${x}" y="${T+ph+22}" text-anchor="end" transform="rotate(-38 ${x} ${T+ph+22})" font-size="10" fill="#64748b">${esc(r.date.slice(5))}</text>`}});
    series.forEach((s,si)=>{const points=data.map((r,i)=>{const x=L+(data.length>1?i*step:pw/2),v=Number(r[s.key])||0,y=T+ph-v/axisMax[s.axis||0]*ph;return{x,y,v}});out+=`<polyline fill="none" stroke="${colors[si]}" stroke-width="2.5" points="${points.map(p=>p.x+','+p.y).join(' ')}"/>`;points.forEach((p,index)=>out+=`<circle data-chart-point="1" data-series-index="${si}" data-point-index="${index}" cx="${p.x}" cy="${p.y}" r="${data.length<16?3.5:2.5}" fill="${colors[si]}"/>`)});
    out+=`<line data-hover-line="1" x1="${L}" y1="${T}" x2="${L}" y2="${T+ph}" stroke="#64748b" stroke-width="1" stroke-dasharray="4 4" visibility="hidden"/></svg><div class="chart-tooltip" data-chart-tooltip="1" hidden></div>`;
    box.innerHTML=out;
    const svg=box.querySelector('svg'),tooltip=box.querySelector('[data-chart-tooltip]'),hoverLine=svg.querySelector('[data-hover-line]'),circles=[...svg.querySelectorAll('[data-chart-point]')];
    const hideTooltip=()=>{tooltip.hidden=true;hoverLine.setAttribute('visibility','hidden');circles.forEach(circle=>circle.setAttribute('r',data.length<16?'3.5':'2.5'))};
    svg.addEventListener('pointermove',event=>{const svgRect=svg.getBoundingClientRect(),scaleX=w/svgRect.width,localX=(event.clientX-svgRect.left)*scaleX,index=data.length>1?Math.max(0,Math.min(data.length-1,Math.round((localX-L)/step))):0,x=L+(data.length>1?index*step:pw/2),row=data[index];hoverLine.setAttribute('x1',String(x));hoverLine.setAttribute('x2',String(x));hoverLine.setAttribute('visibility','visible');circles.forEach(circle=>circle.setAttribute('r',Number(circle.dataset.pointIndex)===index?'5':(data.length<16?'3.5':'2.5')));tooltip.innerHTML=`<b>${esc(row.date)}</b>`+series.map((s,si)=>`<div class="chart-tooltip-row"><span class="chart-tooltip-dot" style="background:${colors[si]}"></span><span>${esc(s.name)}：</span><strong>${esc(displayValue(row[s.key]))}</strong></div>`).join('');tooltip.dataset.tooltipDate=row.date;tooltip.hidden=false;const boxRect=box.getBoundingClientRect(),tipRect=tooltip.getBoundingClientRect();let left=event.clientX-boxRect.left+14,top=event.clientY-boxRect.top+12;if(left+tipRect.width>box.clientWidth-8)left=event.clientX-boxRect.left-tipRect.width-14;if(top+tipRect.height>box.clientHeight-8)top=event.clientY-boxRect.top-tipRect.height-12;tooltip.style.left=Math.max(8,left)+'px';tooltip.style.top=Math.max(8,top)+'px'});
    svg.addEventListener('pointerleave',hideTooltip);
  }
  function render(){const data=filteredRows(),extra=filteredSupplemental(),sum=names=>data.reduce((a,r)=>a+num(r,names),0),extraSum=names=>extra.reduce((a,r)=>a+num(r,names),0),sales=sum(['销售额','Ordered Product Sales']),orders=sum(['总订单','Units Ordered','订单量']),sessions=sum(['Sessions']),pv=sum(['PV','Page Views']),adSpend=sum(['广告花费','Spend'])+extraSum(['adSpend','广告花费']),adSales=sum(['广告销售额','Sales'])+extraSum(['adSales','广告销售额']);const acos=adSales>0?adSpend/adSales*100:0;const metrics=[['记录数',data.length,'筛选后商品数据行'],['销售额',sales.toFixed(2),'筛选区间累计'],['订单量',orders.toFixed(0),'筛选区间累计'],['Sessions',sessionsAvailable?sessions.toFixed(0):'暂不可用',sessionsAvailable?'业务流量':'当前数据源未提供'],['PV',pageViewsAvailable?pv.toFixed(0):'暂不可用',pageViewsAvailable?'页面浏览量':'当前数据源未提供'],['广告花费',adSpend.toFixed(2),extra.length?'含店铺级未分配广告':'筛选区间累计'],['广告销售额',adSales.toFixed(2),extra.length?'含店铺级未分配广告':'筛选区间累计'],['ACoS',acos.toFixed(2)+'%','广告花费 ÷ 广告销售额']];cards.innerHTML=metrics.map(x=>`<div class="metric"><span>${esc(x[0])}</span><strong>${esc(x[1])}</strong><small>${esc(x[2])}</small></div>`).join('');const g=grouped(data,extra);if(trafficAvailable){chart('trafficChart','业务流量趋势',g,[{name:'Sessions',key:'sessions'},{name:'PV',key:'pv'}],{left:'流量'})}else{trafficChart.innerHTML='<div class="chart-empty">业务流量趋势：Sessions / PV 暂不可用<br>不会将缺失数据绘制为 0</div>'}chart('salesChart','销量与销售额趋势',g,[{name:'订单量',key:'orders'},{name:'销售额',key:'sales',axis:1}],{left:'销量',right:'销售额'});chart('adsChart','广告花费与销售额趋势',g,[{name:'广告花费',key:'adSpend'},{name:'广告销售额',key:'adSales',axis:1}],{left:'广告花费',right:'广告销售额'});chart('inventoryChart','库存趋势',g,[{name:'FBA可售库存',key:'fba'},{name:'总库存',key:'inventory'}],{left:'库存'});filterSummary.textContent=`${store.value||'全部店铺'} · ${market.value||'全部站点'} · ${search.value||'全部商品'} · ${startDate.value||'最早'} 至 ${endDate.value||'最新'}`;const cols=['日期','shop_name','marketplace','SKU','ASIN','产品名称','Sessions','PV','总订单','销售额','广告花费','广告销售额','FBA可售库存','总库存'];const cell=(r,c)=>c==='Sessions'&&!sessionsAvailable?'-':c==='PV'&&!pageViewsAvailable?'-':r[c];count.textContent=`显示 ${data.length} 条商品记录${extra.length?`；另有 ${extra.length} 条店铺级广告汇总计入广告总额`:''}`;head.innerHTML='<tr>'+cols.map(c=>`<th>${esc(c)}</th>`).join('')+'</tr>';body.innerHTML=data.slice(0,2000).map(r=>'<tr>'+cols.map(c=>`<td>${esc(cell(r,c))}</td>`).join('')+'</tr>').join('');const q=payload.quality||[];quality.innerHTML=q.map(r=>`<div class="quality-row"><b>${esc(r['类别']||'问题')}</b>：${esc(r['内容']||JSON.stringify(r))}</div>`).join('')||'<span class="muted">未发现数据质量问题</span>'}
  ['store','market','startDate','endDate'].forEach(id=>document.getElementById(id).addEventListener('change',render));search.addEventListener('input',render);window.addEventListener('resize',()=>requestAnimationFrame(render));render();
})();
</script></body></html>'''


def write_html_dashboard(
    excel_path,
    output_dir=None,
    *,
    dashboard_context: dict | None = None,
):
    excel = Path(excel_path).resolve()
    output_root = Path(output_dir or excel.parent).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    payload = _payload(excel, dashboard_context=dashboard_context)
    json_path = output_root / "dashboard_data.json"
    html_path = output_root / "dashboard.html"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    html_path.write_text(
        HTML_TEMPLATE.replace("__DATA__", _safe_json(payload)),
        encoding="utf-8",
    )
    return html_path, json_path
