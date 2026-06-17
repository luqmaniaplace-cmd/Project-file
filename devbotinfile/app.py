#!/usr/bin/env python3
"""GitHub File Manager v2 — Full Control"""

import os, json, base64, threading, time, subprocess, webbrowser, sys
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
import urllib.request, urllib.error, re

PORT = 8765
HOST = '127.0.0.1'
URL = 'http://' + HOST + ':' + str(PORT)
CONFIG_FILE = Path.home() / '.ghfm_config.json'
DEBUG = True  # Termux terminal debug

def log(msg, level='INFO'):
    if DEBUG:
        colors = {'INFO':'\033[36m', 'OK':'\033[32m', 'ERROR':'\033[31m', 'WARN':'\033[33m', 'REQ':'\033[35m'}
        reset = '\033[0m'
        c = colors.get(level, '')
        print(c + '[' + level + '] ' + str(msg) + reset, flush=True)

# ── CONFIG ──
def load_config():
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text())
    except: pass
    return {}

def save_config(data):
    CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))

# ── GITHUB API ──
def gh_request(path, token, method='GET', body=None):
    url = 'https://api.github.com' + path
    bearer = 'Bearer ' + token
    headers = {
        'Authorization': bearer,
        'Accept': 'application/vnd.github+json',
        'Content-Type': 'application/json',
        'User-Agent': 'GHFM/2.0',
        'X-GitHub-Api-Version': '2022-11-28'
    }
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            resp_body = r.read().decode()
            log('GH ' + method + ' ' + path + ' -> ' + str(r.status), 'OK')
            return json.loads(resp_body) if resp_body.strip() else {}, r.status
    except urllib.error.HTTPError as e:
        log('GH ERROR ' + str(e.code) + ' ' + path, 'ERROR')
        try: return json.loads(e.read().decode()), e.code
        except: return {'error': str(e)}, e.code
    except Exception as e:
        log('GH EXCEPTION: ' + str(e), 'ERROR')
        return {'error': str(e)}, 0

def get_repos(token):
    repos = []
    page = 1
    while True:
        data, status = gh_request('/user/repos?per_page=100&page=' + str(page) + '&sort=updated', token)
        if status == 401: raise Exception('Invalid token')
        if status == 403: raise Exception('Rate limited or no permission')
        if status != 200 or not isinstance(data, list) or not data: break
        for r in data:
            repos.append({
                'name': r.get('name',''),
                'full_name': r.get('full_name',''),
                'private': r.get('private', False),
                'updated_at': r.get('updated_at',''),
                'has_pages': r.get('has_pages', False),
                'default_branch': r.get('default_branch','main')
            })
        if len(data) < 100: break
        page += 1
    return repos

def get_branches(token, repo):
    data, status = gh_request('/repos/' + repo + '/branches', token)
    if status == 200 and isinstance(data, list):
        return [b['name'] for b in data]
    return ['main']

def get_file_sha(token, repo, path):
    data, status = gh_request('/repos/' + repo + '/contents/' + path, token)
    if status == 200 and isinstance(data, dict):
        return data.get('sha', '')
    return ''

def save_to_github(token, repo, path, content, message=None):
    if isinstance(content, str):
        content_bytes = content.encode('utf-8', errors='replace')
    else:
        content_bytes = content
    b64 = base64.b64encode(content_bytes).decode()
    sha = get_file_sha(token, repo, path)
    fname = path.split('/')[-1]
    body = {'message': message or ('Add: ' + fname), 'content': b64}
    if sha: body['sha'] = sha
    data, status = gh_request('/repos/' + repo + '/contents/' + path, token, 'PUT', body)
    return status in (200, 201), data

def create_repo(token, name, private=False, description=''):
    body = {
        'name': name,
        'private': private,
        'description': description,
        'auto_init': True
    }
    data, status = gh_request('/user/repos', token, 'POST', body)
    return status == 201, data

def delete_repo(token, full_name):
    data, status = gh_request('/repos/' + full_name, token, 'DELETE')
    return status == 204, data

def enable_pages(token, repo, branch='main'):
    body = {'source': {'branch': branch, 'path': '/'}}
    data, status = gh_request('/repos/' + repo + '/pages', token, 'POST', body)
    if status in (201, 409):
        return True, data
    return False, data

def disable_pages(token, repo):
    data, status = gh_request('/repos/' + repo + '/pages', token, 'DELETE')
    return status == 204, data

def get_pages_info(token, repo):
    data, status = gh_request('/repos/' + repo + '/pages', token)
    if status == 200:
        return data
    return None

def list_files(token, repo, path=''):
    data, status = gh_request('/repos/' + repo + '/contents/' + path, token)
    if status == 200 and isinstance(data, list):
        return data
    return []

def delete_file(token, repo, path, sha, message=None):
    body = {'message': message or ('Delete: ' + path.split('/')[-1]), 'sha': sha}
    data, status = gh_request('/repos/' + repo + '/contents/' + path, token, 'DELETE', body)
    return status == 200, data

def fetch_url_content(url, token=None):
    gh_blob = re.match(r'https://github\.com/([^/]+/[^/]+)/blob/([^/]+)/(.+)', url)
    if gh_blob:
        repo, branch, path = gh_blob.groups()
        if token:
            data, status = gh_request('/repos/' + repo + '/contents/' + path + '?ref=' + branch, token)
            if status == 200 and isinstance(data, dict) and 'content' in data:
                content = base64.b64decode(data['content'].replace('\n','')).decode('utf-8','replace')
                return content, data.get('name','file.txt')
        url = 'https://raw.githubusercontent.com/' + repo + '/' + branch + '/' + path
    raw_url = url.replace('github.com','raw.githubusercontent.com').replace('/blob/','/')
    for try_url in [raw_url, url]:
        try:
            req = urllib.request.Request(try_url, headers={'User-Agent': 'GHFM/2.0'})
            with urllib.request.urlopen(req, timeout=10) as r:
                content = r.read().decode('utf-8', errors='replace')
                name = try_url.split('/')[-1].split('?')[0] or 'file.txt'
                log('Fetched: ' + name + ' (' + str(len(content)) + ' chars)', 'OK')
                return content, name
        except Exception as e:
            log('Fetch failed: ' + str(e), 'WARN')
            continue
    return None, None

# ── HTML ──
HTML = r'''<!DOCTYPE html>
<html lang="ur" dir="auto">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>GitHub File Manager v2</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0a0b10;--s1:#0f1018;--s2:#161820;--s3:#1e2030;
  --border:#252840;--ac:#6c63ff;--a2:#a78bfa;--a3:#38bdf8;
  --tx:#dde1f0;--mu:#6b7280;--di:#374151;
  --gn:#22d3a8;--rd:#f87171;--or:#fbbf24;
}
body{font-family:'Segoe UI',sans-serif;background:var(--bg);color:var(--tx);min-height:100vh;}
.hdr{display:flex;align-items:center;gap:12px;padding:12px 16px;background:var(--s1);border-bottom:1px solid var(--border);position:sticky;top:0;z-index:20;}
.logo{width:36px;height:36px;border-radius:10px;background:linear-gradient(135deg,var(--ac),var(--a3));display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0;}
.hdr h1{font-size:14px;font-weight:700;}
.hdr-sub{font-size:11px;color:var(--mu);}
.nav{display:flex;background:var(--s1);border-bottom:1px solid var(--border);overflow-x:auto;}
.nb{padding:11px 13px;font-size:12px;font-weight:600;color:var(--mu);cursor:pointer;white-space:nowrap;border:none;border-bottom:2px solid transparent;background:none;}
.nb.on{color:var(--ac);border-bottom-color:var(--ac);}
.nb:hover{color:var(--tx);}
.wrap{padding:12px;max-width:560px;margin:0 auto;}
.pg{display:none;} .pg.on{display:block;}
.card{background:var(--s1);border:1px solid var(--border);border-radius:12px;padding:14px;margin-bottom:10px;}
.ct{font-size:11px;font-weight:700;margin-bottom:10px;display:flex;align-items:center;gap:5px;}
.inp{background:var(--s2);border:1px solid var(--border);border-radius:8px;padding:9px 11px;color:var(--tx);font-size:13px;width:100%;outline:none;font-family:inherit;}
.inp:focus{border-color:var(--ac);}
.inp::placeholder{color:var(--di);}
select.inp{cursor:pointer;}
.f{margin-bottom:9px;}
.lb{font-size:11px;color:var(--mu);margin-bottom:3px;}
.btn{padding:9px 13px;border-radius:8px;border:none;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;display:flex;align-items:center;gap:5px;justify-content:center;width:100%;}
.bp{background:linear-gradient(135deg,var(--ac),var(--a2));color:#fff;}
.bb{background:linear-gradient(135deg,#1d4ed8,var(--a3));color:#fff;}
.bg{background:linear-gradient(135deg,#065f46,var(--gn));color:#fff;}
.br{background:linear-gradient(135deg,#7f1d1d,var(--rd));color:#fff;}
.bs{padding:7px 11px;font-size:12px;background:var(--s3);color:var(--mu);border:1px solid var(--border);width:auto;}
.btn:disabled{opacity:.35;cursor:not-allowed;}
.btn:not(:disabled):hover{opacity:.85;}
.rw{display:flex;gap:7px;} .rw .btn{flex:1;}
.dz{border:2px dashed var(--border);border-radius:11px;padding:20px 14px;text-align:center;cursor:pointer;position:relative;margin-bottom:9px;}
.dz:hover{border-color:var(--ac);background:rgba(108,99,255,.05);}
.dz input{position:absolute;inset:0;opacity:0;cursor:pointer;width:100%;height:100%;}
.di2{font-size:26px;margin-bottom:4px;}
.dt{font-size:13px;font-weight:700;}
.ds{font-size:11px;color:var(--mu);margin-top:2px;}
.pw{background:var(--border);border-radius:3px;height:4px;overflow:hidden;margin:7px 0;}
.pb{height:100%;border-radius:3px;transition:width .3s;width:0%;}
.pp{background:linear-gradient(90deg,var(--ac),var(--a2));}
.pbl{background:linear-gradient(90deg,#1d4ed8,var(--a3));}
.pg2{background:linear-gradient(90deg,#065f46,var(--gn));}
.log{background:var(--s2);border:1px solid var(--border);border-radius:8px;padding:9px;max-height:220px;overflow-y:auto;font-size:11px;line-height:1.9;}
.li{display:flex;gap:4px;padding:1px 0;word-break:break-all;}
.ok{color:var(--gn);} .er{color:var(--rd);} .inf{color:var(--mu);} .wn{color:var(--or);}
.sts{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-bottom:9px;}
.sc{background:var(--s2);border:1px solid var(--border);border-radius:8px;padding:9px;text-align:center;}
.sv{font-size:20px;font-weight:700;color:var(--a2);}
.sl{font-size:10px;color:var(--mu);margin-top:1px;}
.fl{max-height:130px;overflow-y:auto;margin-bottom:7px;}
.fi{display:flex;align-items:center;gap:7px;padding:5px 7px;background:var(--s2);border-radius:6px;margin-bottom:3px;font-size:12px;}
.fn{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.fsz{color:var(--mu);font-size:10px;}
.ts{font-size:10px;color:var(--di);margin-top:3px;min-height:13px;}
.repo-card{padding:10px;background:var(--s2);border:1px solid var(--border);border-radius:9px;margin-bottom:6px;}
.repo-name{font-weight:600;color:var(--tx);font-size:13px;}
.repo-meta{font-size:10px;color:var(--mu);margin-top:2px;}
.repo-actions{display:flex;gap:5px;margin-top:7px;flex-wrap:wrap;}
.ra{padding:4px 9px;font-size:11px;border-radius:6px;border:none;cursor:pointer;font-weight:600;}
.ra-blue{background:rgba(56,189,248,.15);color:var(--a3);border:1px solid rgba(56,189,248,.3);}
.ra-green{background:rgba(34,211,168,.15);color:var(--gn);border:1px solid rgba(34,211,168,.3);}
.ra-red{background:rgba(248,113,113,.15);color:var(--rd);border:1px solid rgba(248,113,113,.3);}
.ra-purple{background:rgba(108,99,255,.15);color:var(--a2);border:1px solid rgba(108,99,255,.3);}
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:100;align-items:center;justify-content:center;padding:16px;}
.modal.on{display:flex;}
.modal-box{background:var(--s1);border:1px solid var(--border);border-radius:14px;padding:18px;width:100%;max-width:360px;}
.modal-title{font-size:14px;font-weight:700;margin-bottom:12px;}
::-webkit-scrollbar{width:3px}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
</style>
</head>
<body>

<div class="hdr">
  <div class="logo">📁</div>
  <div>
    <h1>GitHub File Manager v2</h1>
    <div class="hdr-sub" id="hdrSub">Ready — Debug in Termux terminal</div>
  </div>
</div>

<div class="nav">
  <button class="nb on" onclick="pg('settings',this)">⚙️ Settings</button>
  <button class="nb" onclick="pg('url',this)">🔗 URL</button>
  <button class="nb" onclick="pg('files',this)">📄 Files</button>
  <button class="nb" onclick="pg('folder',this)">📂 Folder</button>
  <button class="nb" onclick="pg('repos',this)">🗂 Repos</button>
  <button class="nb" onclick="pg('manage',this)">🛠 Manage</button>
  <button class="nb" onclick="pg('log',this)">📋 Log</button>
</div>

<div class="wrap">

<!-- SETTINGS -->
<div class="pg on" id="pg-settings">
  <div class="card">
    <div class="ct" style="color:var(--a2)">⚙️ GitHub Settings</div>
    <div class="f">
      <div class="lb">GitHub Token (repo scope)</div>
      <div style="display:flex;gap:6px;">
        <input class="inp" id="token" type="password" placeholder="ghp_..." style="flex:1;">
        <button class="btn bs" id="reloadBtn" onclick="loadRepos()">🔄</button>
      </div>
      <div class="ts" id="tokenTS"></div>
    </div>
    <div class="f">
      <div class="lb">Select Repository</div>
      <select class="inp" id="repoSel" onchange="onRepoChange()">
        <option value="">-- Enter token and click 🔄 --</option>
      </select>
    </div>
    <div class="f">
      <div class="lb">Branch</div>
      <select class="inp" id="branchSel">
        <option value="main">main</option>
        <option value="master">master</option>
      </select>
    </div>
    <div class="f">
      <div class="lb">Default Folder (empty = root)</div>
      <input class="inp" id="defFolder" placeholder="e.g. knowledge">
    </div>
    <div class="rw">
      <button class="btn bp" onclick="saveConfig()">💾 Save</button>
      <button class="btn bs" onclick="clearConfig()">🗑</button>
    </div>
    <div class="ts" id="configTS"></div>
  </div>
  <div class="card">
    <div class="ct" style="color:var(--gn)">How to use</div>
    <div style="font-size:12px;color:var(--mu);line-height:2.1;">
      1. Token → 🔄 → Select repo → 💾 Save<br>
      2. <b style="color:var(--tx)">🔗 URL</b> — any link → GitHub<br>
      3. <b style="color:var(--tx)">📄 Files</b> — upload files<br>
      4. <b style="color:var(--tx)">📂 Folder</b> — upload folder<br>
      5. <b style="color:var(--tx)">🗂 Repos</b> — view + manage repos<br>
      6. <b style="color:var(--tx)">🛠 Manage</b> — files, pages, new repo<br>
      7. <b style="color:var(--tx)">Debug:</b> check Termux terminal!
    </div>
  </div>
</div>

<!-- URL -->
<div class="pg" id="pg-url">
  <div class="card">
    <div class="ct" style="color:var(--ac)">🔗 Save URL to GitHub</div>
    <div class="f">
      <div class="lb">URLs — one per line</div>
      <textarea class="inp" id="urlInput" rows="5" style="resize:vertical;line-height:1.7;"
        placeholder="https://github.com/mdn/content/blob/main/...&#10;https://raw.githubusercontent.com/..."></textarea>
    </div>
    <div class="f">
      <div class="lb">Save to folder (empty = default)</div>
      <input class="inp" id="urlFolder" placeholder="e.g. html-docs">
    </div>
    <div class="pw" id="urlPW" style="display:none"><div class="pb pp" id="urlPB"></div></div>
    <div class="ts" id="urlPT"></div>
    <button class="btn bp" id="urlBtn" onclick="doURLs()">⚡ Load & Save to GitHub</button>
  </div>
</div>

<!-- FILES -->
<div class="pg" id="pg-files">
  <div class="card">
    <div class="ct" style="color:var(--a3)">📄 Upload Files</div>
    <div class="dz" id="fileDz">
      <input type="file" id="fileInp" multiple onchange="prevFiles(this.files,'f')">
      <div class="di2">📄</div>
      <div class="dt">Drop files or click</div>
      <div class="ds">txt · md · json · csv · py · js · zip</div>
    </div>
    <div class="fl" id="fileList"></div>
    <div class="f">
      <div class="lb">Save to folder</div>
      <input class="inp" id="filesFolder" placeholder="e.g. my-notes">
    </div>
    <div class="pw" id="filesPW" style="display:none"><div class="pb pbl" id="filesPB"></div></div>
    <div class="ts" id="filesPT"></div>
    <button class="btn bb" id="filesBtn" onclick="doFiles()" disabled>📤 Upload to GitHub</button>
  </div>
</div>

<!-- FOLDER -->
<div class="pg" id="pg-folder">
  <div class="card">
    <div class="ct" style="color:var(--gn)">📂 Upload Full Folder</div>
    <div class="dz" onclick="document.getElementById('folderInp').click()">
      <input type="file" id="folderInp" webkitdirectory multiple onchange="prevFiles(this.files,'folder')">
      <div class="di2">📂</div>
      <div class="dt">Click to select folder</div>
      <div class="ds">All files → GitHub with folder structure</div>
    </div>
    <div class="fl" id="folderList"></div>
    <div class="f">
      <div class="lb">GitHub folder name (empty = original)</div>
      <input class="inp" id="folderName" placeholder="e.g. my-project">
    </div>
    <div class="pw" id="folderPW" style="display:none"><div class="pb pg2" id="folderPB"></div></div>
    <div class="ts" id="folderPT"></div>
    <button class="btn bg" id="folderBtn" onclick="doFolder()" disabled>📂 Upload to GitHub</button>
  </div>
</div>

<!-- REPOS -->
<div class="pg" id="pg-repos">
  <div class="card">
    <div class="ct" style="color:var(--or)">🗂 Your Repositories</div>
    <button class="btn bs" onclick="loadRepos(true)" style="width:100%;margin-bottom:9px;">🔄 Refresh</button>
    <div id="reposList" style="font-size:12px;color:var(--mu);text-align:center;padding:14px;">
      Enter token in Settings then refresh
    </div>
  </div>
</div>

<!-- MANAGE -->
<div class="pg" id="pg-manage">
  <!-- Create New Repo -->
  <div class="card">
    <div class="ct" style="color:var(--gn)">➕ Create New Repository</div>
    <div class="f">
      <div class="lb">Repository Name</div>
      <input class="inp" id="newRepoName" placeholder="e.g. my-project">
    </div>
    <div class="f">
      <div class="lb">Description (optional)</div>
      <input class="inp" id="newRepoDesc" placeholder="My awesome project">
    </div>
    <div class="f" style="display:flex;align-items:center;gap:8px;">
      <input type="checkbox" id="newRepoPrivate" style="width:16px;height:16px;">
      <label for="newRepoPrivate" style="font-size:13px;">Private Repository</label>
    </div>
    <div class="ts" id="newRepoTS"></div>
    <button class="btn bg" onclick="createRepo()">➕ Create Repository</button>
  </div>

  <!-- GitHub Pages -->
  <div class="card">
    <div class="ct" style="color:var(--a3)">🌐 GitHub Pages</div>
    <div class="f">
      <div class="lb">Repository</div>
      <input class="inp" id="pagesRepo" placeholder="username/repo">
    </div>
    <div class="f">
      <div class="lb">Branch</div>
      <input class="inp" id="pagesBranch" placeholder="main" value="main">
    </div>
    <div class="ts" id="pagesTS"></div>
    <div class="rw">
      <button class="btn bg" onclick="enablePages()">✅ Enable Pages</button>
      <button class="btn br" onclick="disablePages()">❌ Disable</button>
    </div>
    <button class="btn bs" onclick="checkPages()" style="margin-top:7px;width:100%;">ℹ️ Check Status</button>
  </div>

  <!-- Browse Files -->
  <div class="card">
    <div class="ct" style="color:var(--a2)">📂 Browse Repository Files</div>
    <div class="f">
      <div class="lb">Repository (username/repo)</div>
      <input class="inp" id="browseRepo" placeholder="username/repo">
    </div>
    <div class="f">
      <div class="lb">Path (empty = root)</div>
      <input class="inp" id="browsePath" placeholder="e.g. docs">
    </div>
    <button class="btn bp" onclick="browseFiles()" style="margin-bottom:7px;">📂 Browse Files</button>
    <div id="filesBrowser" style="font-size:12px;color:var(--mu);text-align:center;">
      Enter repo and click Browse
    </div>
  </div>
</div>

<!-- LOG -->
<div class="pg" id="pg-log">
  <div class="card">
    <div class="ct" style="color:var(--a2)">📋 Activity Log</div>
    <div class="sts">
      <div class="sc"><div class="sv" id="stS">0</div><div class="sl">✅ Saved</div></div>
      <div class="sc"><div class="sv" id="stF">0</div><div class="sl">❌ Failed</div></div>
      <div class="sc"><div class="sv" id="stSz">0</div><div class="sl">📦 KB</div></div>
    </div>
    <button class="btn bs" onclick="clearLog()" style="width:100%;margin-bottom:7px;">🗑 Clear</button>
    <div class="log" id="logBox"><div class="li inf"><span>ℹ️</span><span>No activity yet</span></div></div>
  </div>
</div>

</div><!-- wrap -->

<!-- CONFIRM MODAL -->
<div class="modal" id="confirmModal">
  <div class="modal-box">
    <div class="modal-title" id="confirmTitle">Confirm</div>
    <div id="confirmMsg" style="font-size:13px;color:var(--mu);margin-bottom:14px;"></div>
    <div class="rw">
      <button class="btn bs" onclick="closeModal()">Cancel</button>
      <button class="btn br" id="confirmBtn">Delete</button>
    </div>
  </div>
</div>

<script>
var fileQ = [], folderQ = [], stats = {s:0, f:0, sz:0};
var reposCache = [];

// == NAV ==
function pg(id, el) {
  var pages = document.querySelectorAll('.pg');
  for (var i=0; i<pages.length; i++) pages[i].classList.remove('on');
  var btns = document.querySelectorAll('.nb');
  for (var i=0; i<btns.length; i++) btns[i].classList.remove('on');
  var t = document.getElementById('pg-' + id);
  if (t) t.classList.add('on');
  if (el) el.classList.add('on');
}

// == API ==
function apiCall(path, method, body, callback) {
  method = method || 'GET';
  var opts = {method: method, headers: {'Content-Type': 'application/json'}};
  if (body) { try { opts.body = JSON.stringify(body); } catch(e) {} }
  fetch(path, opts)
    .then(function(r) { return r.text(); })
    .then(function(txt) {
      try { callback(null, JSON.parse(txt)); }
      catch(e) { callback(e, null); }
    })
    .catch(function(e) { callback(e, null); });
}

function ts(id, msg, color) {
  var el = document.getElementById(id);
  if (!el) return;
  el.textContent = msg;
  el.style.color = color || 'var(--mu)';
  setTimeout(function() { el.textContent = ''; }, 5000);
}

// == SETTINGS ==
function loadConfig() {
  apiCall('/api/config', 'GET', null, function(err, d) {
    if (err || !d) return;
    if (d.token) document.getElementById('token').value = d.token;
    if (d.folder) document.getElementById('defFolder').value = d.folder;
    if (d.repo) {
      var sel = document.getElementById('repoSel');
      sel.innerHTML = '<option value="' + d.repo + '">' + d.repo + '</option>';
      document.getElementById('browseRepo').value = d.repo;
      document.getElementById('pagesRepo').value = d.repo;
    }
    if (d.branch) {
      document.getElementById('branchSel').innerHTML = '<option value="' + d.branch + '">' + d.branch + '</option>';
      document.getElementById('pagesBranch').value = d.branch;
    }
    if (d.token && d.repo) {
      document.getElementById('hdrSub').textContent = 'Connected: ' + d.repo;
      ts('tokenTS', 'Config loaded', 'var(--gn)');
    }
  });
}

function loadRepos(showPage) {
  var token = document.getElementById('token').value.trim();
  if (!token) { ts('tokenTS', 'Enter token first!', 'var(--or)'); return; }
  ts('tokenTS', 'Loading...', 'var(--mu)');
  document.getElementById('reloadBtn').disabled = true;
  apiCall('/api/repos', 'POST', {token: token}, function(err, d) {
    document.getElementById('reloadBtn').disabled = false;
    if (err || !d) { ts('tokenTS', 'Fetch error', 'var(--rd)'); return; }
    if (d.error) { ts('tokenTS', d.error, 'var(--rd)'); return; }
    if (!d.repos || !d.repos.length) { ts('tokenTS', 'No repos found', 'var(--or)'); return; }
    reposCache = d.repos;
    var sel = document.getElementById('repoSel');
    var html = '';
    for (var i=0; i<d.repos.length; i++) {
      var r = d.repos[i];
      html += '<option value="' + r.full_name + '">' + r.name + (r.private?' (private)':'') + '</option>';
    }
    sel.innerHTML = html;
    ts('tokenTS', 'Found ' + d.repos.length + ' repos', 'var(--gn)');
    onRepoChange();
    renderRepos(d.repos);
    if (showPage) pg('repos', document.querySelectorAll('.nb')[4]);
  });
}

function onRepoChange() {
  var token = document.getElementById('token').value.trim();
  var repo = document.getElementById('repoSel').value.trim();
  if (!token || !repo) return;
  document.getElementById('browseRepo').value = repo;
  document.getElementById('pagesRepo').value = repo;
  apiCall('/api/branches', 'POST', {token: token, repo: repo}, function(err, d) {
    if (err || !d || !d.branches) return;
    var sel = document.getElementById('branchSel');
    var html = '';
    for (var i=0; i<d.branches.length; i++) {
      html += '<option value="' + d.branches[i] + '">' + d.branches[i] + '</option>';
    }
    sel.innerHTML = html;
    document.getElementById('pagesBranch').value = d.branches[0] || 'main';
  });
}

function saveConfig() {
  var token = document.getElementById('token').value.trim();
  var repo = document.getElementById('repoSel').value.trim();
  var branch = document.getElementById('branchSel').value.trim() || 'main';
  var folder = document.getElementById('defFolder').value.trim();
  if (!token) { ts('configTS', 'Token required!', 'var(--or)'); return; }
  apiCall('/api/config', 'POST', {token:token, repo:repo, branch:branch, folder:folder}, function(err, d) {
    if (d && d.ok) {
      ts('configTS', 'Saved!', 'var(--gn)');
      document.getElementById('hdrSub').textContent = 'Connected: ' + repo;
    } else {
      ts('configTS', 'Save failed', 'var(--rd)');
    }
  });
}

function clearConfig() {
  apiCall('/api/config', 'DELETE', null, function() {
    document.getElementById('token').value = '';
    document.getElementById('defFolder').value = '';
    document.getElementById('repoSel').innerHTML = '<option value="">-- Enter token and click 🔄 --</option>';
    ts('configTS', 'Cleared', 'var(--rd)');
    document.getElementById('hdrSub').textContent = 'Ready';
  });
}

// == REPOS LIST ==
function renderRepos(repos) {
  var box = document.getElementById('reposList');
  var html = '';
  for (var i=0; i<repos.length; i++) {
    var r = repos[i];
    html += '<div class="repo-card">';
    html += '<div class="repo-name">' + r.name + (r.private?' <span style="font-size:10px;color:var(--or)">(private)</span>':'') + '</div>';
    html += '<div class="repo-meta">' + r.full_name + ' · ' + (r.has_pages ? '🌐 Pages ON' : 'No Pages') + '</div>';
    html += '<div class="repo-actions">';
    html += '<button class="ra ra-blue" onclick="selectRepo(\'' + r.full_name + '\')">✅ Select</button>';
    html += '<button class="ra ra-purple" onclick="browseRepo(\'' + r.full_name + '\')">📂 Browse</button>';
    html += '<button class="ra ra-green" onclick="pagesAction(\'' + r.full_name + '\',\'' + r.default_branch + '\',' + r.has_pages + ')">🌐 Pages</button>';
    html += '<button class="ra ra-red" onclick="confirmDelete(\'' + r.full_name + '\')">🗑 Delete</button>';
    html += '</div></div>';
  }
  box.innerHTML = html || '<div style="text-align:center;color:var(--mu);padding:12px">No repos</div>';
}

function selectRepo(fullName) {
  var sel = document.getElementById('repoSel');
  var found = false;
  for (var i=0; i<sel.options.length; i++) {
    if (sel.options[i].value === fullName) { sel.selectedIndex = i; found = true; break; }
  }
  if (!found) {
    var opt = document.createElement('option');
    opt.value = fullName; opt.text = fullName;
    sel.add(opt); sel.value = fullName;
  }
  onRepoChange();
  pg('settings', document.querySelectorAll('.nb')[0]);
  ts('configTS', 'Repo selected: ' + fullName, 'var(--gn)');
}

function browseRepo(fullName) {
  document.getElementById('browseRepo').value = fullName;
  document.getElementById('browsePath').value = '';
  pg('manage', document.querySelectorAll('.nb')[5]);
  browseFiles();
}

// == CREATE REPO ==
function createRepo() {
  var token = document.getElementById('token').value.trim();
  var name = document.getElementById('newRepoName').value.trim();
  var desc = document.getElementById('newRepoDesc').value.trim();
  var priv = document.getElementById('newRepoPrivate').checked;
  if (!token) { ts('newRepoTS', 'Enter token in Settings first', 'var(--or)'); return; }
  if (!name) { ts('newRepoTS', 'Enter repo name', 'var(--or)'); return; }
  ts('newRepoTS', 'Creating...', 'var(--mu)');
  apiCall('/api/create-repo', 'POST', {token:token, name:name, description:desc, private:priv}, function(err, d) {
    if (err || !d) { ts('newRepoTS', 'Error', 'var(--rd)'); return; }
    if (d.ok) {
      ts('newRepoTS', 'Created: ' + d.full_name, 'var(--gn)');
      document.getElementById('newRepoName').value = '';
      document.getElementById('newRepoDesc').value = '';
      addLog('Repo created: ' + d.full_name, 'ok');
      loadRepos(false);
    } else {
      ts('newRepoTS', d.error || 'Failed', 'var(--rd)');
    }
  });
}

// == PAGES ==
function pagesAction(repo, branch, hasPages) {
  document.getElementById('pagesRepo').value = repo;
  document.getElementById('pagesBranch').value = branch;
  pg('manage', document.querySelectorAll('.nb')[5]);
  if (hasPages) checkPages(); else enablePages();
}

function enablePages() {
  var token = document.getElementById('token').value.trim();
  var repo = document.getElementById('pagesRepo').value.trim();
  var branch = document.getElementById('pagesBranch').value.trim() || 'main';
  if (!token || !repo) { ts('pagesTS', 'Fill repo and ensure token is set', 'var(--or)'); return; }
  ts('pagesTS', 'Enabling Pages...', 'var(--mu)');
  apiCall('/api/pages', 'POST', {token:token, repo:repo, branch:branch, action:'enable'}, function(err, d) {
    if (d && d.ok) {
      ts('pagesTS', 'Pages enabled! URL: ' + (d.url||'check GitHub'), 'var(--gn)');
      addLog('Pages enabled: ' + repo, 'ok');
    } else {
      ts('pagesTS', (d && d.error) || 'Failed', 'var(--rd)');
    }
  });
}

function disablePages() {
  var token = document.getElementById('token').value.trim();
  var repo = document.getElementById('pagesRepo').value.trim();
  if (!token || !repo) { ts('pagesTS', 'Fill repo first', 'var(--or)'); return; }
  ts('pagesTS', 'Disabling Pages...', 'var(--mu)');
  apiCall('/api/pages', 'POST', {token:token, repo:repo, action:'disable'}, function(err, d) {
    if (d && d.ok) {
      ts('pagesTS', 'Pages disabled', 'var(--gn)');
      addLog('Pages disabled: ' + repo, 'wn');
    } else {
      ts('pagesTS', (d && d.error) || 'Failed', 'var(--rd)');
    }
  });
}

function checkPages() {
  var token = document.getElementById('token').value.trim();
  var repo = document.getElementById('pagesRepo').value.trim();
  if (!token || !repo) { ts('pagesTS', 'Fill repo first', 'var(--or)'); return; }
  apiCall('/api/pages', 'POST', {token:token, repo:repo, action:'check'}, function(err, d) {
    if (d && d.info) {
      ts('pagesTS', 'Status: ' + d.info.status + ' | URL: ' + d.info.html_url, 'var(--gn)');
    } else if (d && d.error) {
      ts('pagesTS', 'Pages not enabled: ' + d.error, 'var(--or)');
    }
  });
}

// == BROWSE FILES ==
function browseFiles() {
  var token = document.getElementById('token').value.trim();
  var repo = document.getElementById('browseRepo').value.trim();
  var path = document.getElementById('browsePath').value.trim();
  if (!token || !repo) { document.getElementById('filesBrowser').textContent = 'Enter repo and ensure token is set in Settings'; return; }
  document.getElementById('filesBrowser').textContent = 'Loading...';
  apiCall('/api/browse', 'POST', {token:token, repo:repo, path:path}, function(err, d) {
    if (err || !d || d.error) {
      document.getElementById('filesBrowser').textContent = 'Error: ' + (d && d.error || 'failed');
      return;
    }
    if (!d.files || !d.files.length) {
      document.getElementById('filesBrowser').textContent = 'Empty folder';
      return;
    }
    var html = '';
    if (path) {
      var parent = path.split('/').slice(0,-1).join('/');
      html += '<div class="fi" style="cursor:pointer;" onclick="navPath(\'' + parent + '\')">';
      html += '<span>⬆️</span><span class="fn">.. (back)</span></div>';
    }
    for (var i=0; i<d.files.length; i++) {
      var f = d.files[i];
      var isDir = f.type === 'dir';
      html += '<div class="fi">';
      html += '<span>' + (isDir ? '📁' : '📄') + '</span>';
      if (isDir) {
        html += '<span class="fn" style="cursor:pointer;color:var(--a2);" onclick="navPath(\'' + f.path + '\')">' + f.name + '</span>';
      } else {
        html += '<span class="fn">' + f.name + '</span>';
        html += '<span class="fsz">' + fsize(f.size||0) + '</span>';
        html += '<button onclick="delFile(\'' + repo + '\',\'' + f.path + '\',\'' + f.sha + '\')" style="background:none;border:none;color:var(--rd);cursor:pointer;font-size:13px;padding:0 3px;">🗑</button>';
      }
      html += '</div>';
    }
    document.getElementById('filesBrowser').innerHTML = html;
  });
}

function navPath(path) {
  document.getElementById('browsePath').value = path;
  browseFiles();
}

function delFile(repo, path, sha) {
  showConfirm('Delete File', 'Delete: ' + path + '?', function() {
    var token = document.getElementById('token').value.trim();
    apiCall('/api/delete-file', 'POST', {token:token, repo:repo, path:path, sha:sha}, function(err, d) {
      if (d && d.ok) { addLog('Deleted: ' + path, 'ok'); browseFiles(); }
      else addLog('Delete failed: ' + path, 'er');
    });
  });
}

// == DELETE REPO ==
function confirmDelete(fullName) {
  showConfirm('Delete Repository', 'Permanently delete "' + fullName + '"? This CANNOT be undone!', function() {
    var token = document.getElementById('token').value.trim();
    apiCall('/api/delete-repo', 'POST', {token:token, repo:fullName}, function(err, d) {
      if (d && d.ok) { addLog('Repo deleted: ' + fullName, 'wn'); loadRepos(false); }
      else addLog('Delete failed: ' + (d && d.error || ''), 'er');
    });
  });
}

// == MODAL ==
var _confirmCb = null;
function showConfirm(title, msg, cb) {
  _confirmCb = cb;
  document.getElementById('confirmTitle').textContent = title;
  document.getElementById('confirmMsg').textContent = msg;
  document.getElementById('confirmModal').classList.add('on');
  document.getElementById('confirmBtn').onclick = function() { closeModal(); if(_confirmCb) _confirmCb(); };
}
function closeModal() { document.getElementById('confirmModal').classList.remove('on'); }

// == FILE PREVIEW ==
var ICONS = {txt:'📄',md:'📝',json:'📋',csv:'📊',py:'🐍',js:'📜',html:'🌐',css:'🎨',zip:'📦',xml:'🔧',sql:'🗄️',sh:'🖥️',java:'☕'};
function ficon(n) { return ICONS[n.split('.').pop().toLowerCase()] || '📄'; }
function fsize(b) { return b<1024 ? b+'B' : b<1048576 ? (b/1024).toFixed(1)+'K' : (b/1048576).toFixed(1)+'M'; }

function prevFiles(files, type) {
  var arr = Array.from(files);
  if (type === 'f') { fileQ = arr; renderFL('fileList', arr); document.getElementById('filesBtn').disabled = !arr.length; }
  else { folderQ = arr; renderFL('folderList', arr); document.getElementById('folderBtn').disabled = !arr.length; }
}
function renderFL(id, files) {
  var box = document.getElementById(id);
  var html = '';
  var show = files.slice(0, 10);
  for (var i=0; i<show.length; i++) {
    var f = show[i];
    html += '<div class="fi"><span>' + ficon(f.name) + '</span><span class="fn">' + (f.webkitRelativePath||f.name) + '</span><span class="fsz">' + fsize(f.size) + '</span></div>';
  }
  if (files.length > 10) html += '<div class="li inf" style="padding:3px 7px">...' + (files.length-10) + ' more</div>';
  box.innerHTML = html;
}

// == UPLOAD FUNCTIONS ==
function doURLs() {
  var raw = document.getElementById('urlInput').value.trim();
  if (!raw) { addLog('No URLs', 'wn'); return; }
  var urls = raw.split('\n');
  var clean = [];
  for (var i=0; i<urls.length; i++) { var u = urls[i].trim(); if(u) clean.push(u); }
  var folder = document.getElementById('urlFolder').value.trim();
  document.getElementById('urlBtn').disabled = true;
  document.getElementById('urlPW').style.display = 'block';
  var i = 0;
  function next() {
    if (i >= clean.length) {
      document.getElementById('urlPB').style.width = '100%';
      document.getElementById('urlPT').textContent = 'Done!';
      document.getElementById('urlBtn').disabled = false;
      updateStats();
      setTimeout(function(){document.getElementById('urlPW').style.display='none';}, 2000);
      return;
    }
    document.getElementById('urlPB').style.width = Math.floor((i/clean.length)*100) + '%';
    document.getElementById('urlPT').textContent = (i+1)+'/'+clean.length+': '+clean[i].split('/').pop();
    apiCall('/api/save-url', 'POST', {url: clean[i], folder: folder}, function(err, d) {
      if (d && d.ok) { addLog(d.name + ' -> ' + d.path, 'ok'); stats.s++; stats.sz += d.size||0; }
      else { addLog('Failed: ' + (d && d.error || clean[i].split('/').pop()), 'er'); stats.f++; }
      i++; setTimeout(next, 350);
    });
  }
  next();
}

function doFiles() {
  if (!fileQ.length) return;
  var folder = document.getElementById('filesFolder').value.trim();
  document.getElementById('filesBtn').disabled = true;
  document.getElementById('filesPW').style.display = 'block';
  var i = 0;
  function next() {
    if (i >= fileQ.length) {
      document.getElementById('filesPB').style.width = '100%';
      document.getElementById('filesPT').textContent = 'Done!';
      document.getElementById('filesBtn').disabled = false;
      fileQ = []; document.getElementById('fileList').innerHTML = '';
      updateStats();
      setTimeout(function(){document.getElementById('filesPW').style.display='none';}, 2000);
      return;
    }
    document.getElementById('filesPB').style.width = Math.floor((i/fileQ.length)*100) + '%';
    document.getElementById('filesPT').textContent = (i+1)+'/'+fileQ.length+': '+fileQ[i].name;
    uploadFile(fileQ[i], folder, null, function() { i++; setTimeout(next, 200); });
  }
  next();
}

function doFolder() {
  if (!folderQ.length) return;
  var override = document.getElementById('folderName').value.trim();
  document.getElementById('folderBtn').disabled = true;
  document.getElementById('folderPW').style.display = 'block';
  var i = 0;
  function next() {
    if (i >= folderQ.length) {
      document.getElementById('folderPB').style.width = '100%';
      document.getElementById('folderPT').textContent = 'Done!';
      document.getElementById('folderBtn').disabled = false;
      folderQ = []; document.getElementById('folderList').innerHTML = '';
      updateStats();
      setTimeout(function(){document.getElementById('folderPW').style.display='none';}, 2000);
      return;
    }
    document.getElementById('folderPB').style.width = Math.floor((i/folderQ.length)*100) + '%';
    document.getElementById('folderPT').textContent = (i+1)+'/'+folderQ.length+': '+folderQ[i].name;
    var f = folderQ[i];
    var relPath = f.webkitRelativePath || f.name;
    var parts = relPath.split('/');
    if (override && parts.length > 0) parts[0] = override;
    uploadFile(f, null, parts.join('/'), function() { i++; setTimeout(next, 150); });
  }
  next();
}

function uploadFile(file, folder, fullPath, cb) {
  var reader = new FileReader();
  reader.onload = function(e) {
    var content = e.target.result;
    apiCall('/api/save-file', 'POST', {name:file.name, content:content, folder:folder, fullPath:fullPath}, function(err, d) {
      if (d && d.ok) { addLog(file.name + ' -> ' + d.path, 'ok'); stats.s++; stats.sz += file.size||0; }
      else { addLog('Failed: ' + file.name, 'er'); stats.f++; }
      if (cb) cb();
    });
  };
  reader.readAsText(file, 'utf-8');
}

function addLog(msg, type) {
  type = type || 'inf';
  var box = document.getElementById('logBox');
  var empty = box.querySelector('.inf');
  if (empty && empty.textContent.indexOf('No activity') >= 0) empty.parentNode.removeChild(empty);
  var d = document.createElement('div');
  d.className = 'li ' + type;
  var ic = {ok:'✅',er:'❌',wn:'⚠️',inf:'ℹ️'};
  d.innerHTML = '<span>' + (ic[type]||'•') + '</span><span>' + msg + '</span>';
  box.appendChild(d);
  box.scrollTop = box.scrollHeight;
}

function clearLog() {
  document.getElementById('logBox').innerHTML = '<div class="li inf"><span>ℹ️</span><span>No activity yet</span></div>';
  stats = {s:0, f:0, sz:0};
  updateStats();
}

function updateStats() {
  document.getElementById('stS').textContent = stats.s;
  document.getElementById('stF').textContent = stats.f;
  document.getElementById('stSz').textContent = (stats.sz/1024).toFixed(1);
}

loadConfig();
</script>
</body>
</html>'''

# ── HTTP HANDLER ──
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log('HTTP ' + fmt % args, 'REQ')

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST,DELETE,OPTIONS')
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html):
        body = html.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def get_body(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length > 0:
                raw = self.rfile.read(length).decode('utf-8', errors='replace')
                return json.loads(raw) if raw.strip() else {}
        except Exception as e:
            log('get_body error: ' + str(e), 'ERROR')
        return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST,DELETE,OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        cfg = load_config()
        if path == '/':
            self.send_html(HTML)
        elif path == '/api/config':
            self.send_json({
                'token': cfg.get('token',''),
                'repo': cfg.get('repo',''),
                'branch': cfg.get('branch','main'),
                'folder': cfg.get('folder','')
            })
        else:
            self.send_json({'error': 'Not found'}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        body = self.get_body()
        cfg = load_config()
        log('POST ' + path + ' body_keys=' + str(list(body.keys())), 'REQ')

        if path == '/api/config':
            save_config(body)
            self.send_json({'ok': True})

        elif path == '/api/repos':
            token = (body.get('token') or cfg.get('token','')).strip()
            if not token:
                self.send_json({'error': 'No token provided', 'repos': []})
                return
            try:
                repos = get_repos(token)
                log('Repos fetched: ' + str(len(repos)), 'OK')
                self.send_json({'repos': repos, 'count': len(repos)})
            except Exception as e:
                log('get_repos error: ' + str(e), 'ERROR')
                self.send_json({'error': str(e), 'repos': []})

        elif path == '/api/branches':
            token = (body.get('token') or cfg.get('token','')).strip()
            repo = (body.get('repo') or cfg.get('repo','')).strip()
            branches = get_branches(token, repo) if token and repo else ['main']
            self.send_json({'branches': branches})

        elif path == '/api/create-repo':
            token = (body.get('token') or cfg.get('token','')).strip()
            name = body.get('name','').strip()
            if not token or not name:
                self.send_json({'ok': False, 'error': 'Token and name required'})
                return
            ok, data = create_repo(token, name, body.get('private', False), body.get('description',''))
            if ok:
                self.send_json({'ok': True, 'full_name': data.get('full_name','')})
            else:
                msg = data.get('message', 'Failed')
                self.send_json({'ok': False, 'error': msg})

        elif path == '/api/delete-repo':
            token = (body.get('token') or cfg.get('token','')).strip()
            repo = body.get('repo','').strip()
            if not token or not repo:
                self.send_json({'ok': False, 'error': 'Token and repo required'})
                return
            ok, _ = delete_repo(token, repo)
            self.send_json({'ok': ok})

        elif path == '/api/pages':
            token = (body.get('token') or cfg.get('token','')).strip()
            repo = body.get('repo','').strip()
            action = body.get('action','check')
            if not token or not repo:
                self.send_json({'ok': False, 'error': 'Token and repo required'})
                return
            if action == 'enable':
                branch = body.get('branch','main')
                ok, data = enable_pages(token, repo, branch)
                url = data.get('html_url','') if ok else ''
                self.send_json({'ok': ok, 'url': url, 'error': data.get('message','') if not ok else ''})
            elif action == 'disable':
                ok, _ = disable_pages(token, repo)
                self.send_json({'ok': ok})
            else:
                info = get_pages_info(token, repo)
                if info:
                    self.send_json({'ok': True, 'info': info})
                else:
                    self.send_json({'ok': False, 'error': 'Pages not enabled'})

        elif path == '/api/browse':
            token = (body.get('token') or cfg.get('token','')).strip()
            repo = body.get('repo','').strip()
            fpath = body.get('path','').strip()
            if not token or not repo:
                self.send_json({'error': 'Token and repo required'})
                return
            files = list_files(token, repo, fpath)
            self.send_json({'files': files})

        elif path == '/api/delete-file':
            token = (body.get('token') or cfg.get('token','')).strip()
            repo = body.get('repo','').strip()
            fpath = body.get('path','').strip()
            sha = body.get('sha','').strip()
            if not token or not repo or not fpath or not sha:
                self.send_json({'ok': False, 'error': 'Missing required fields'})
                return
            ok, _ = delete_file(token, repo, fpath, sha)
            self.send_json({'ok': ok})

        elif path == '/api/save-url':
            token = cfg.get('token','').strip()
            repo = cfg.get('repo','').strip()
            if not token or not repo:
                self.send_json({'ok': False, 'error': 'Settings not configured'})
                return
            url = body.get('url','').strip()
            folder = (body.get('folder','') or cfg.get('folder','')).strip()
            content, name = fetch_url_content(url, token)
            if not content:
                self.send_json({'ok': False, 'error': 'Could not fetch URL'})
                return
            safe = name.replace(' ','_')
            gh_path = (folder + '/' + safe) if folder else safe
            ok, _ = save_to_github(token, repo, gh_path, content)
            self.send_json({'ok': ok, 'name': name, 'path': gh_path, 'size': len(content)})

        elif path == '/api/save-file':
            token = cfg.get('token','').strip()
            repo = cfg.get('repo','').strip()
            if not token or not repo:
                self.send_json({'ok': False, 'error': 'Settings not configured'})
                return
            name = body.get('name','file.txt')
            content = body.get('content','')
            folder = (body.get('folder','') or cfg.get('folder','')).strip()
            full_path = body.get('fullPath')
            if full_path:
                gh_path = full_path
            else:
                safe = name.replace(' ','_')
                gh_path = (folder + '/' + safe) if folder else safe
            ok, _ = save_to_github(token, repo, gh_path, content)
            self.send_json({'ok': ok, 'path': gh_path})

        else:
            self.send_json({'error': 'Not found'}, 404)

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path == '/api/config':
            if CONFIG_FILE.exists(): CONFIG_FILE.unlink()
            self.send_json({'ok': True})

# ── TERMINAL ──
def print_menu():
    print('\n' + '-'*40)
    print('  GitHub File Manager v2')
    print('  ' + URL)
    print('  Debug: watch this terminal')
    print('-'*40)
    print('  1. Open in browser')
    print('  2. Show link')
    print('  3. Copy link (Termux)')
    print('  4. Show config')
    print('  5. Clear config')
    print('  6. Toggle debug log')
    print('-'*40)
    print('  Ctrl+C to stop')
    print('-'*40)

def open_browser():
    try:
        if os.path.exists('/data/data/com.termux'):
            subprocess.run(['termux-open-url', URL], check=False)
        else:
            webbrowser.open(URL)
        print('  Opening: ' + URL)
    except:
        print('  Open manually: ' + URL)

def copy_link():
    try:
        subprocess.run(['termux-clipboard-set', URL], check=False)
        print('  Copied: ' + URL)
    except:
        print('  Link: ' + URL)

def show_config():
    cfg = load_config()
    if cfg:
        print('  Repo:   ' + cfg.get('repo','not set'))
        print('  Branch: ' + cfg.get('branch','main'))
        print('  Folder: ' + cfg.get('folder','root'))
        print('  Token:  ' + ('SET' if cfg.get('token') else 'NOT SET'))
    else:
        print('  No config saved.')

def handle_menu():
    global DEBUG
    time.sleep(0.5)
    print_menu()
    while True:
        try:
            choice = input('\n  Option [1-6]: ').strip()
            if choice == '1': open_browser()
            elif choice == '2': print('  ' + URL)
            elif choice == '3': copy_link()
            elif choice == '4': show_config()
            elif choice == '5':
                if CONFIG_FILE.exists(): CONFIG_FILE.unlink(); print('  Config cleared.')
                else: print('  No config found.')
            elif choice == '6':
                DEBUG = not DEBUG
                print('  Debug: ' + ('ON' if DEBUG else 'OFF'))
            else:
                print('  ' + URL)
        except (EOFError, KeyboardInterrupt):
            break

def main():
    global DEBUG
    print('=' * 40)
    print('  GitHub File Manager v2')
    print('  Starting server...')
    server = HTTPServer((HOST, PORT), Handler)
    t = threading.Thread(target=server.serve_forever)
    t.daemon = True
    t.start()
    print('  Running: ' + URL)
    print('  Debug logs will appear here')
    print('=' * 40)
    time.sleep(0.3)
    open_browser()
    try:
        handle_menu()
    except KeyboardInterrupt:
        pass
    finally:
        print('\n  Shutting down...')
        server.shutdown()

if __name__ == '__main__':
    main()
