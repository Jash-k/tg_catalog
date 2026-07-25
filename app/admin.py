import asyncio
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select, delete, func
from .config import settings
from .db import Session, Content, Unmatched, ScanTracker

HTML = '''<!doctype html><html><head><meta charset="utf-8"><title>TMDB Catalog Admin</title><style>body{font:14px system-ui;margin:28px;background:#101318;color:#eee}button,input{padding:8px;margin:3px;border-radius:6px;border:1px solid #555;background:#1c222b;color:#fff}button{cursor:pointer}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}.card{background:#1a2029;padding:16px;border-radius:10px}.muted{color:#9aa4b2}table{width:100%;border-collapse:collapse;margin-top:12px}td,th{padding:7px;border-bottom:1px solid #333;text-align:left}.danger{background:#8b2635}</style></head><body><h1>Telegram TMDB Catalog</h1><p class="muted">Metadata and scanner administration. Telegram file links and sizes are never shown or stored.</p><input id="token" type="password" placeholder="ADMIN_TOKEN"><button onclick="save()">Save token</button><button onclick="scan()">Start scan</button><div id="status">Loading...</div><h2>Recent content</h2><input id="q" placeholder="Search title"><button onclick="load()">Search</button><div id="content"></div><h2>Unmatched review</h2><div id="unmatched"></div><script>const t=()=>localStorage.adminToken||document.querySelector('#token').value;function save(){localStorage.adminToken=document.querySelector('#token').value;load()}async function api(u,o={}){o.headers={...(o.headers||{}),'X-Admin-Token':t(),'Content-Type':'application/json'};let r=await fetch(u,o);if(!r.ok)throw Error(await r.text());return r.json()}async function scan(){await api('/admin/api/scan',{method:'POST'});load()}async function del(id){if(confirm('Delete this metadata record?')){await api('/admin/api/content/'+id,{method:'DELETE'});load()}}async function load(){try{let s=await api('/admin/api/status');document.querySelector('#status').innerHTML='<div class="grid">'+[['Scan',s.scan_running?'RUNNING':'IDLE'],['Content',s.content_count],['Unmatched',s.unmatched_count],['Last scan',s.last_scan_completed||'never'],['Stats',JSON.stringify(s.current_scan_stats||{})]].map(x=>'<div class="card"><b>'+x[0]+'</b><br>'+x[1]+'</div>').join('')+'</div>';let c=await api('/admin/api/content?q='+encodeURIComponent(document.querySelector('#q').value));document.querySelector('#content').innerHTML='<table><tr><th>Title</th><th>Type</th><th>Catalog</th><th>Year</th><th></th></tr>'+c.items.map(x=>'<tr><td>'+x.title+'</td><td>'+x.media_type+'</td><td>'+x.catalog+'</td><td>'+ (x.year||'')+'</td><td><button class="danger" onclick="del('+x.id+')">Delete</button></td></tr>').join('')+'</table>';let u=await api('/admin/api/unmatched');document.querySelector('#unmatched').innerHTML='<table><tr><th>Raw name</th><th>Cleaned</th><th>Reason</th></tr>'+u.items.map(x=>'<tr><td>'+x.raw_name+'</td><td>'+x.cleaned_title+'</td><td>'+x.reason+'</td></tr>').join('')+'</table>'}catch(e){document.querySelector('#status').innerHTML='<p>'+e+'</p>'}}load();setInterval(load,60000)</script></body></html>'''

def register_admin(app: FastAPI, scanner):
    def check(token: str | None):
        if not settings.admin_token or token != settings.admin_token:
            raise HTTPException(401, 'Invalid admin token')
    @app.get('/admin', response_class=HTMLResponse)
    async def admin_page(): return HTML
    @app.get('/admin/api/status')
    async def admin_status(x_admin_token: str | None = Header(default=None)):
        check(x_admin_token)
        async with Session() as db:
            content_count = (await db.execute(select(func.count(Content.id)))).scalar_one()
            unmatched_count = (await db.execute(select(func.count(Unmatched.id)))).scalar_one()
            trackers = (await db.execute(select(ScanTracker))).scalars().all()
        return {'scan_running':scanner.running,'last_scan_started':scanner.last_scan_started,'last_scan_completed':scanner.last_scan_completed,'last_scan_error':scanner.last_scan_error,'current_scan_stats':scanner.current_scan_stats,'last_scan_stats':scanner.last_scan_stats,'content_count':content_count,'unmatched_count':unmatched_count,'trackers':[{'channel_key':x.channel_key,'last_message_id':x.last_message_id,'complete':x.historical_scan_completed,'last_scan_at':x.last_scan_at} for x in trackers]}
    @app.get('/admin/api/content')
    async def admin_content(request: Request, x_admin_token: str | None = Header(default=None)):
        check(x_admin_token); q=request.query_params.get('q','')
        async with Session() as db:
            stmt=select(Content).order_by(Content.discovered_at.desc().nullslast()).limit(200)
            if q: stmt=select(Content).where(Content.title.ilike(f'%{q}%')).order_by(Content.discovered_at.desc().nullslast()).limit(200)
            rows=(await db.execute(stmt)).scalars().all()
        return {'items':[{'id':x.id,'title':x.title,'media_type':x.media_type,'catalog':x.catalog,'year':x.year,'tmdb_id':x.tmdb_id} for x in rows]}
    @app.delete('/admin/api/content/{content_id}')
    async def admin_delete_content(content_id:int, x_admin_token: str | None = Header(default=None)):
        check(x_admin_token)
        async with Session() as db: await db.execute(delete(Content).where(Content.id==content_id)); await db.commit()
        return {'ok':True}
    @app.get('/admin/api/unmatched')
    async def admin_unmatched(x_admin_token: str | None = Header(default=None)):
        check(x_admin_token)
        async with Session() as db: rows=(await db.execute(select(Unmatched).order_by(Unmatched.created_at.desc()).limit(200))).scalars().all()
        return {'items':[{'id':x.id,'raw_name':x.raw_name,'cleaned_title':x.cleaned_title,'reason':x.reason,'media_type':x.media_type} for x in rows]}
    @app.post('/admin/api/scan')
    async def admin_scan(x_admin_token: str | None = Header(default=None)):
        check(x_admin_token); asyncio.create_task(scanner.scan()); return {'ok':True}
