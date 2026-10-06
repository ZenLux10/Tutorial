import argparse
import html as htmlmod
import json
import re
import socket
import sys
import threading
import time
import webbrowser
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

GUIDES = [

    ("index.html", "HTML", "html", "#ff7a59", "🧱"),
    ("css.html", "CSS", "css", "#38bdf8", "🎨"),
    ("javascript.html", "JavaScript", "js", "#f1fa8c", "⚡"),
]
WPM = 200


class GuideParser(HTMLParser):

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.sections = []
        self.snippets = []
        self.gp_skip = 0
        self.gp_in_title = False
        self.gp_in_h2 = False
        self.gp_in_pre = False
        self.gp_in_header = False
        self.gp_cur_title = ""
        self.gp_grab_title = False
        self.gp_pre_buf = []
        self.gp_h2_count = 0
        self.gp_cur = {"anchor": "", "heading": "Introduzione", "text": []}

    def gp_flush(self):
        text = re.sub(r"\s+", " ", " ".join(self.gp_cur["text"])).strip()
        if text or self.gp_cur["anchor"]:
            self.sections.append({**self.gp_cur, "text": text})

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style"):
            self.gp_skip += 1
        elif tag == "title":
            self.gp_in_title = True
        elif tag == "h2":
            self.gp_flush()
            self.gp_h2_count += 1
            anchor = a.get("id") or f"h2-{self.gp_h2_count}"
            self.gp_cur = {"anchor": anchor, "heading": "", "text": []}
            self.gp_in_h2 = True
        elif tag == "div" and "code-header" in (a.get("class") or ""):
            self.gp_in_header, self.gp_cur_title = True, ""
        elif tag == "span" and self.gp_in_header:
            self.gp_grab_title = True
        elif tag == "pre":
            self.gp_in_pre, self.gp_pre_buf = True, []
            self.gp_in_header = False

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.gp_skip = max(0, self.gp_skip - 1)
        elif tag == "title":
            self.gp_in_title = False
        elif tag == "h2":
            self.gp_in_h2 = False
        elif tag == "span":
            self.gp_grab_title = False
        elif tag == "pre":
            code = "".join(self.gp_pre_buf).strip("\r\n")
            self.snippets.append(
                {"title": self.gp_cur_title or self.gp_cur["heading"] or "Snippet", "code": code}
            )
            self.gp_cur["text"].append(code)
            self.gp_in_pre = False

    def handle_data(self, data):
        if self.gp_skip:
            return
        if self.gp_in_title:
            self.title += data
        elif self.gp_in_pre:
            self.gp_pre_buf.append(data)
        elif self.gp_grab_title:
            self.gp_cur_title += data
        elif self.gp_in_h2:
            self.gp_cur["heading"] += data
        else:
            self.gp_cur["text"].append(data)

    def close(self):
        super().close()
        self.gp_flush()


def sniff_lang(code, default):
    s = code.lstrip()
    if s.startswith("<"):
        return "html"
    return default


class Library:

    def __init__(self, root: Path):
        self.root = root
        self._sig = None
        self._lock = threading.Lock()
        self.docs = []

    def signature(self):
        return max((p.stat().st_mtime for p in self._paths()), default=0)

    def _paths(self):
        return [self.root / g[0] for g in GUIDES if (self.root / g[0]).exists()]

    def refresh(self):
        sig = self.signature()
        with self._lock:
            if sig == self._sig:
                return
            docs = []
            for fname, label, lang, color, emoji in GUIDES:
                p = self.root / fname
                if not p.exists():
                    continue
                raw = p.read_text(encoding="utf-8", errors="replace")
                parser = GuideParser()
                parser.feed(raw)
                parser.close()
                words = sum(len(s["text"].split()) for s in parser.sections)
                docs.append(
                    {
                        "file": fname, "label": label, "lang": lang, "color": color,
                        "emoji": emoji, "title": parser.title.strip() or label,
                        "sections": parser.sections, "snippets": parser.snippets,
                        "words": words, "minutes": max(1, round(words / WPM)),
                    }
                )
            self.docs, self._sig = docs, sig


    def search(self, query, limit=12):
        self.refresh()
        terms = [t for t in re.findall(r"\w+", query.lower()) if t]
        if not terms:
            return []
        results = []
        for d in self.docs:
            for s in d["sections"]:
                head, text = s["heading"].lower(), s["text"].lower()
                if not all(t in head or t in text for t in terms):
                    continue
                score = sum(text.count(t) + 8 * head.count(t) for t in terms)
                if query.lower() in text:
                    score += 15
                pos = min((text.find(t) for t in terms if t in text), default=0)
                start = max(0, pos - 60)
                snippet = s["text"][start : start + 170].strip()
                results.append(
                    {
                        "file": d["file"], "label": d["label"], "color": d["color"],
                        "heading": s["heading"].strip() or d["title"],
                        "anchor": s["anchor"], "snippet": ("…" if start else "") + snippet,
                        "score": score,
                    }
                )
        results.sort(key=lambda r: -r["score"])
        return results[:limit]

    def all_snippets(self):
        self.refresh()
        out = []
        for d in self.docs:
            for i, sn in enumerate(d["snippets"]):
                out.append(
                    {
                        "file": d["file"], "idx": i, "guide": d["label"], "title": sn["title"],
                        "lang": sniff_lang(sn["code"], d["lang"]), "code": sn["code"],
                    }
                )
        return out


OVERLAY = r"""
<style id="cs-style">
#cs-progress{position:fixed;top:0;left:0;height:3px;width:0;z-index:99998;
  background:linear-gradient(90deg,#ff79c6,#8be9fd,#50fa7b);transition:width .1s}
#cs-fab{position:fixed;right:18px;bottom:18px;z-index:99997;display:flex;gap:8px}
#cs-fab a,#cs-fab button{font:600 13px system-ui,sans-serif;color:#f8f8f2;background:#282a36ee;
  border:1px solid #44475a;border-radius:999px;padding:9px 14px;cursor:pointer;text-decoration:none;
  backdrop-filter:blur(6px);box-shadow:0 6px 20px #0008}
#cs-fab a:hover,#cs-fab button:hover{border-color:#bd93f9}
#cs-fab kbd{background:#44475a;border-radius:4px;padding:1px 6px;margin-left:6px;font-size:11px}
.cs-try{margin-left:8px;font:600 12px system-ui,sans-serif;color:#0b0b10!important;background:#50fa7b;
  padding:4px 10px;border-radius:4px;text-decoration:none!important}
#cs-modal{position:fixed;inset:0;background:#000a;z-index:99999;display:none;
  align-items:flex-start;justify-content:center;padding-top:12vh}
#cs-modal.open{display:flex}
#cs-box{width:min(680px,92vw);background:#1e1e2e;border:1px solid #44475a;border-radius:12px;
  box-shadow:0 20px 60px #000c;overflow:hidden;font-family:system-ui,sans-serif;color:#f8f8f2}
#cs-q{width:100%;padding:16px 18px;font-size:17px;background:transparent;border:0;
  border-bottom:1px solid #44475a;color:#f8f8f2;outline:none}
#cs-res{max-height:55vh;overflow:auto}
.cs-r{padding:11px 18px;cursor:pointer;border-left:3px solid transparent}
.cs-r.sel{background:#282a36;border-left-color:var(--c,#bd93f9)}
.cs-r b{font-size:14px}.cs-r small{display:block;color:#a9a9c0;margin-top:3px;font-size:12.5px;line-height:1.4}
.cs-tag{font-size:10.5px;font-weight:700;padding:2px 7px;border-radius:4px;margin-right:8px;
  color:#0b0b10;background:var(--c)}
.cs-r mark{background:#f1fa8c33;color:#f1fa8c;border-radius:2px}
#cs-empty{padding:20px 18px;color:#8a8fa8;font-size:14px}
</style>
<div id="cs-progress"></div>
<div id="cs-fab">
  <a href="/">🏠 Hub</a><a href="/playground">🧪 Playground</a>
  <button id="cs-open">🔍 Cerca<kbd>Ctrl K</kbd></button>
</div>
<div id="cs-modal"><div id="cs-box">
  <input id="cs-q" placeholder="Cerca in tutte le guide… (es. flexbox, async, form)" autocomplete="off">
  <div id="cs-res"></div></div></div>
<script>
(function(){
  var $=function(s){return document.querySelector(s)};
  addEventListener('scroll',function(){
    var h=document.documentElement,m=h.scrollHeight-h.clientHeight;
    $('#cs-progress').style.width=(m>0?h.scrollTop/m*100:0)+'%';
  },{passive:true});
  var file=location.pathname.replace(/^\//,'')||'index.html';
  document.querySelectorAll('pre').forEach(function(pre,i){
    var hd=pre.previousElementSibling; if(!hd) return;
    var a=document.createElement('a');a.className='cs-try';a.textContent='▶ Prova';
    a.href='/playground?g='+encodeURIComponent(file)+'&i='+i;a.target='_blank';
    var btn=hd.querySelector('button'); (btn?btn.parentNode:hd).insertBefore(a,btn||null);
  });
  var modal=$('#cs-modal'),q=$('#cs-q'),res=$('#cs-res'),items=[],sel=0,tm;
  function esc(s){return s.replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
  function hl(s,terms){s=esc(s);terms.forEach(function(t){
    if(!t)return;s=s.replace(new RegExp('('+t.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+')','gi'),'<mark>$1</mark>')});return s}
  function open(){modal.classList.add('open');q.value='';res.innerHTML='';q.focus()}
  function close(){modal.classList.remove('open')}
  function go(r){location.href='/'+r.file+(r.anchor?'#'+r.anchor:'');close();
    if(location.pathname.slice(1)===r.file){setTimeout(function(){var e=document.getElementById(r.anchor);if(e)e.scrollIntoView({behavior:'smooth'})},50)}}
  function paint(){
    var terms=q.value.toLowerCase().split(/\s+/);
    res.innerHTML=items.length?'':'<div id="cs-empty">Nessun risultato.</div>';
    items.forEach(function(r,i){var d=document.createElement('div');
      d.className='cs-r'+(i===sel?' sel':'');d.style.setProperty('--c',r.color);
      d.innerHTML='<span class="cs-tag" style="--c:'+r.color+'">'+esc(r.label)+'</span><b>'+hl(r.heading,terms)+'</b><small>'+hl(r.snippet,terms)+'</small>';
      d.onclick=function(){go(r)};res.appendChild(d)});
  }
  q.addEventListener('input',function(){clearTimeout(tm);tm=setTimeout(function(){
    if(!q.value.trim()){items=[];res.innerHTML='';return}
    fetch('/api/search?q='+encodeURIComponent(q.value)).then(function(r){return r.json()})
      .then(function(d){items=d;sel=0;paint()})},120)});
  addEventListener('keydown',function(e){
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();modal.classList.contains('open')?close():open()}
    else if(modal.classList.contains('open')){
      if(e.key==='Escape')close();
      else if(e.key==='ArrowDown'){e.preventDefault();sel=Math.min(sel+1,items.length-1);paint()}
      else if(e.key==='ArrowUp'){e.preventDefault();sel=Math.max(sel-1,0);paint()}
      else if(e.key==='Enter'&&items[sel])go(items[sel]);
    }});
  modal.addEventListener('click',function(e){if(e.target===modal)close()});
  $('#cs-open').onclick=open;
  var last=null;setInterval(function(){fetch('/api/mtime').then(function(r){return r.json()}).then(function(d){
    if(last!==null&&d.t!==last)location.reload();last=d.t}).catch(function(){})},1000);
})();
</script>
"""


def inject(raw_html: str) -> str:
    n = [0]

    def add_id(m):
        n[0] += 1
        attrs = m.group(1) or ""
        if re.search(r"\bid\s*=", attrs):
            return m.group(0)
        return f'<h2 id="h2-{n[0]}"{attrs}>'

    raw_html = re.sub(r"<h2((?:\s[^>]*)?)>", add_id, raw_html)
    if "</body>" in raw_html:
        return raw_html.replace("</body>", OVERLAY + "</body>", 1)
    return raw_html + OVERLAY


BASE_CSS = """
:root{--bg:#121214;--card:#1e1e24;--bd:#323238;--tx:#e1e1e6;--mu:#a9a9b3}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font-family:'Segoe UI',system-ui,sans-serif;line-height:1.5}
"""


def hub_page(lib: Library) -> str:
    lib.refresh()
    cards = ""
    for d in lib.docs:
        cards += f"""
        <a class="card" href="/{d['file']}" style="--c:{d['color']}">
          <div class="em">{d['emoji']}</div>
          <h3>{htmlmod.escape(d['label'])}</h3>
          <p>{htmlmod.escape(d['title'])}</p>
          <div class="stats">
            <span><b>{len(d['sections'])}</b> sezioni</span>
            <span><b>{len(d['snippets'])}</b> esempi</span>
            <span><b>{d['minutes']}</b> min</span>
          </div>
        </a>"""
    total_words = sum(d["words"] for d in lib.docs)
    total_snip = sum(len(d["snippets"]) for d in lib.docs)
    return f"""<!DOCTYPE html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Una Guida Semplice</title>
<style>{BASE_CSS}
.wrap{{max-width:980px;margin:0 auto;padding:60px 22px}}
h1{{font-size:clamp(2.4rem,6vw,3.6rem);letter-spacing:-1.5px;text-align:center}}
h1 span{{background:linear-gradient(90deg,#ff79c6,#8be9fd,#50fa7b);-webkit-background-clip:text;
  background-clip:text;color:transparent}}
.sub{{text-align:center;color:var(--mu);margin:10px 0 34px}}
.search{{display:block;width:100%;padding:16px 20px;font-size:1.1rem;border-radius:12px;
  background:var(--card);border:1px solid var(--bd);color:var(--tx);outline:none}}
.search:focus{{border-color:#bd93f9}}
#out{{margin-top:10px}}
#out a{{display:block;padding:12px 16px;border-radius:8px;color:var(--tx);text-decoration:none;
  border-left:3px solid var(--c);background:var(--card);margin-top:6px}}
#out a:hover{{background:#26262e}}#out small{{display:block;color:var(--mu)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:18px;margin-top:34px}}
.card{{background:var(--card);border:1px solid var(--bd);border-top:3px solid var(--c);border-radius:12px;
  padding:24px;color:var(--tx);text-decoration:none;transition:.2s}}
.card:hover{{transform:translateY(-4px);box-shadow:0 12px 30px #0009;border-color:var(--c)}}
.em{{font-size:2rem}}.card h3{{margin:8px 0 4px;color:var(--c);font-size:1.5rem}}
.card p{{color:var(--mu);font-size:.92rem;min-height:2.8em}}
.stats{{display:flex;gap:14px;margin-top:14px;font-size:.82rem;color:var(--mu)}}.stats b{{color:var(--tx)}}
.pg{{display:flex;align-items:center;gap:18px;margin-top:22px;padding:22px 26px;border-radius:12px;
  background:linear-gradient(120deg,#2a1f3d,#16293b);border:1px solid #44475a;color:var(--tx);text-decoration:none}}
.pg:hover{{border-color:#bd93f9}}.pg .em{{font-size:2.2rem}}.pg p{{color:var(--mu);font-size:.92rem}}
.foot{{text-align:center;color:#6b6b78;font-size:.82rem;margin-top:36px}}
kbd{{background:#2d2d36;border-radius:4px;padding:1px 6px;font-size:.78rem}}
</style></head><body><div class="wrap">
<h1><span>Una Guida Semplice</span></h1>
<p class="sub">{len(lib.docs)} guide · {total_words:,} parole · {total_snip} esempi eseguibili</p>
<input class="search" id="q" placeholder="🔍  Cerca in tutte le guide…" autofocus autocomplete="off">
<div id="out"></div>
<div class="grid">{cards}</div>
<a class="pg" href="/playground"><div class="em">🧪</div><div><h3>Playground</h3>
<p>Scegli un esempio dalle guide, modificalo e guarda il risultato in tempo reale.</p></div></a>
<p class="foot">Premi <kbd>Ctrl</kbd>+<kbd>K</kbd> su qualsiasi guida per cercare · il browser si aggiorna quando salvi i file</p>
</div>
<script>
var q=document.getElementById('q'),out=document.getElementById('out'),t;
function esc(s){{return s.replace(/[&<>"]/g,function(c){{return{{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]}})}}
q.addEventListener('input',function(){{clearTimeout(t);t=setTimeout(function(){{
  if(!q.value.trim()){{out.innerHTML='';return}}
  fetch('/api/search?q='+encodeURIComponent(q.value)).then(function(r){{return r.json()}}).then(function(d){{
    out.innerHTML=d.map(function(r){{return '<a style="--c:'+r.color+'" href="/'+r.file+(r.anchor?'#'+r.anchor:'')+
      '"><b>['+esc(r.label)+'] '+esc(r.heading)+'</b><small>'+esc(r.snippet)+'</small></a>'}}).join('')||
      '<a style="--c:#555"><small>Nessun risultato</small></a>'}})}},120)}});
setInterval(function(){{fetch('/api/mtime').then(function(r){{return r.json()}}).then(function(d){{
  window._l=window._l||d.t;if(d.t!==window._l)location.reload()}})}},1500);
</script></body></html>"""


PLAYGROUND = r"""<!DOCTYPE html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Playground · Alessio</title>
<style>%BASE%
body{height:100vh;display:flex;flex-direction:column}
header{display:flex;gap:12px;align-items:center;padding:10px 16px;background:#191a21;border-bottom:1px solid var(--bd)}
header a{color:var(--tx);text-decoration:none;font-weight:700}
select,button{background:#282a36;color:var(--tx);border:1px solid #44475a;border-radius:6px;padding:7px 10px;font:inherit;font-size:.9rem;cursor:pointer}
select{flex:1;max-width:520px}button:hover{border-color:#bd93f9}
main{flex:1;display:grid;grid-template-columns:1fr 1fr;min-height:0}
@media(max-width:800px){main{grid-template-columns:1fr;grid-template-rows:1fr 1fr}}
.ed{display:flex;flex-direction:column;border-right:1px solid var(--bd);min-height:0}
.tabs{display:flex;background:#191a21}
.tab{padding:9px 18px;cursor:pointer;color:var(--mu);border-bottom:2px solid transparent;font-size:.88rem;font-weight:600}
.tab.on{color:var(--tx);border-color:var(--c)}
textarea{flex:1;background:#282a36;color:#f8f8f2;border:0;outline:0;padding:14px;resize:none;
  font:13.5px/1.55 'Cascadia Code',Consolas,Menlo,monospace;tab-size:2;white-space:pre}
.pv{display:flex;flex-direction:column;min-height:0}
iframe{flex:1;border:0;background:#fff;min-height:0}
#con{height:130px;overflow:auto;background:#0e0e12;border-top:1px solid var(--bd);padding:8px 12px;
  font:12.5px/1.5 Consolas,monospace}
#con div{border-bottom:1px solid #1d1d25;padding:1px 0}.err{color:#ff6e6e}.wrn{color:#f1fa8c}.log{color:#8be9fd}
.lbl{color:var(--mu);font-size:.75rem;padding:4px 12px;background:#191a21;text-transform:uppercase;letter-spacing:1px}
</style></head><body>
<header><a href="/">← Una Guida Semplice</a>
<select id="pick"></select><button id="clr">🧹 Pulisci</button>
<span style="color:var(--mu);font-size:.8rem">Un esempio per volta viene aggiunto al suo editor</span></header>
<main>
 <div class="ed"><div class="tabs" id="tabs"></div>
  <textarea id="ta" spellcheck="false"></textarea></div>
 <div class="pv"><div class="lbl">Anteprima</div><iframe id="fr" sandbox="allow-scripts allow-modals"></iframe>
  <div class="lbl">Console</div><div id="con"></div></div>
</main>
<script>
var LANGS={html:'#ff7a59',css:'#38bdf8',js:'#f1fa8c'},code={html:'',css:'',js:''},cur='html',snips=[];
var ta=document.getElementById('ta'),fr=document.getElementById('fr'),con=document.getElementById('con'),
    tabs=document.getElementById('tabs'),pick=document.getElementById('pick'),tm;
function drawTabs(){tabs.innerHTML='';Object.keys(LANGS).forEach(function(l){
  var d=document.createElement('div');d.className='tab'+(l===cur?' on':'');d.style.setProperty('--c',LANGS[l]);
  d.textContent=l.toUpperCase();d.onclick=function(){code[cur]=ta.value;cur=l;ta.value=code[l];drawTabs()};tabs.appendChild(d)})}
function build(){
  var h=code.html,hook='<script>(function(){function s(t,a){parent.postMessage({cs:1,t:t,m:[].slice.call(a).map(function(x){'+
   'try{return typeof x==="object"?JSON.stringify(x):String(x)}catch(e){return String(x)}}).join(" ")},"*")}'+
   '["log","warn","error","info"].forEach(function(k){var o=console[k];console[k]=function(){s(k,arguments);o.apply(console,arguments)}});'+
   'onerror=function(m,u,l){s("error",[m+" (riga "+l+")"])}})()<\/script>';
  var style='<style>body{font-family:system-ui,sans-serif;padding:12px}'+code.css.replace(/<\/style/gi,'')+'</style>';
  var js='<script>'+code.js.replace(/<\/script/gi,'<\\/script')+'<\/script>';
  if(/<html|<!doctype/i.test(h)){
    h=h.replace(/<\/head>/i,style+'</head>');if(!/<\/head>/i.test(h))h=style+h;
    h=/<\/body>/i.test(h)?h.replace(/<\/body>/i,js+'</body>'):h+js;return hook+h}
  return hook+style+h+js}
function run(){code[cur]=ta.value;con.innerHTML='';fr.srcdoc=build()}
function sched(){clearTimeout(tm);tm=setTimeout(run,350)}
ta.addEventListener('input',sched);
ta.addEventListener('keydown',function(e){if(e.key==='Tab'){e.preventDefault();var s=ta.selectionStart;
  ta.value=ta.value.slice(0,s)+'  '+ta.value.slice(ta.selectionEnd);ta.selectionStart=ta.selectionEnd=s+2}});
addEventListener('message',function(e){if(!e.data||!e.data.cs)return;var d=document.createElement('div');
  d.className=e.data.t==='error'?'err':e.data.t==='warn'?'wrn':'log';d.textContent='› '+e.data.m;con.appendChild(d);con.scrollTop=9e9});
function load(i){var s=snips[i];code[cur]=ta.value;var l=s.lang;
  code[l]=(l==='html'&&code.html&&!/<html|<!doctype/i.test(s.code)?code.html+'\n\n':'')+s.code;
  cur=l;ta.value=code[l];drawTabs();run()}
pick.onchange=function(){load(+pick.value)};
document.getElementById('clr').onclick=function(){code={html:'',css:'',js:''};ta.value='';run()};
fetch('/api/snippets').then(function(r){return r.json()}).then(function(d){snips=d;var g='',og;
  d.forEach(function(s,i){if(s.guide!==g){g=s.guide;og=document.createElement('optgroup');og.label=g;pick.appendChild(og)}
    var o=document.createElement('option');o.value=i;o.textContent='['+s.lang.toUpperCase()+'] '+s.title;og.appendChild(o)});
  var p=new URLSearchParams(location.search),f=p.get('g'),ix=p.get('i');
  var at=d.findIndex(function(s){return s.file===f&&String(s.idx)===ix});
  drawTabs();if(at>=0){pick.value=at;load(at)}else if(d.length){load(0)}});
</script></body></html>""".replace("%BASE%", BASE_CSS)


def make_handler(lib: Library):
    allowed = {g[0] for g in GUIDES}

    class Handler(BaseHTTPRequestHandler):
        server_version = "CodeStudio/1.0"

        def cs_send(self, body, ctype="text/html; charset=utf-8", status=200):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def cs_json(self, obj):
            self.cs_send(json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

        def do_GET(self):
            u = urlparse(self.path)
            path = u.path.rstrip("/") or "/"
            try:
                if path == "/":
                    return self.cs_send(hub_page(lib))
                if path == "/playground":
                    return self.cs_send(PLAYGROUND)
                if path == "/api/search":
                    q = parse_qs(u.query).get("q", [""])[0]
                    return self.cs_json(lib.search(q))
                if path == "/api/snippets":
                    return self.cs_json(lib.all_snippets())
                if path == "/api/mtime":
                    return self.cs_json({"t": lib.signature()})
                name = path.lstrip("/")
                if name in allowed and (lib.root / name).exists():
                    raw = (lib.root / name).read_text(encoding="utf-8", errors="replace")
                    return self.cs_send(inject(raw))
                self.cs_send("<h1>404</h1><p><a href='/'>Torna all'hub</a></p>", status=404)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, fmt, *args):
            if "/api/" in self.path:
                return
            code = args[1] if len(args) > 1 else ""
            col = "\033[32m" if str(code).startswith("2") else "\033[33m"
            print(f"  {col}{code}\033[0m {self.path}")

    return Handler


def find_port(start):
    for p in range(start, start + 20):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise SystemExit("Nessuna porta libera trovata.")


def banner(lib, url):
    c = lambda code, t: f"\033[{code}m{t}\033[0m"
    print("\n" + c("1;35", "  ╔═╗┌─┐┌┬┐┌─┐╔═╗┌┬┐┬ ┬┌┬┐┬┌─┐"))
    print(c("1;36", "  ║  │ │ ││├┤ ╚═╗ │ │ │ │││││ │"))
    print(c("1;32", "  ╚═╝└─┘─┴┘└─┘╚═╝ ┴ └─┘─┴┘┴└─┘") + "\n")
    lib.refresh()
    if not lib.docs:
        print(c("1;31", "  ⚠ Nessuna guida trovata: metti studio.py accanto ai file .html\n"))
    for d in lib.docs:
        print(f"  {d['emoji']} {d['label']:<11} {len(d['sections']):>2} sezioni · "
              f"{len(d['snippets']):>2} esempi · ~{d['minutes']} min di lettura")
    print(f"\n  ➜ {c('1;4;36', url)}   (Ctrl+C per uscire)\n")


def main():
    ap = argparse.ArgumentParser(description="CodeStudio: hub, ricerca e playground per le tue guide.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--dir", default=str(Path(__file__).resolve().parent), help="cartella con i file .html")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    lib = Library(Path(args.dir))
    port = find_port(args.port)
    url = f"http://localhost:{port}"
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(lib))
    banner(lib, url)
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  👋 Alla prossima!\n")
    finally:
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
