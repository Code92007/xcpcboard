// Local proxy server — fetches xcpcio/csgrandeur data server-side (no CORS issues)
// Usage: node server.js  → serves on http://localhost:8765

const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');
const url = require('url');

const PORT = 8765;

const UPSTREAMS = {
  // xcpcio — northeastern
  '/api/dongbei/config':  'https://board.xcpcio.com/data/provincial-contest/2026/northeastern/config.json',
  '/api/dongbei/team':    'https://board.xcpcio.com/data/provincial-contest/2026/northeastern/team.json',
  '/api/dongbei/orgs':    'https://board.xcpcio.com/data/provincial-contest/2026/northeastern/organizations.json',
  '/api/dongbei/run':     'https://board.xcpcio.com/data/provincial-contest/2026/northeastern/run.json',
  // xcpcio — nanchang
  '/api/nanchang/config': 'https://board.xcpcio.com/data/ccpc/12th/nanchang-invitational/config.json',
  '/api/nanchang/team':   'https://board.xcpcio.com/data/ccpc/12th/nanchang-invitational/team.json',
  '/api/nanchang/orgs':   'https://board.xcpcio.com/data/ccpc/12th/nanchang-invitational/organizations.json',
  '/api/nanchang/run':    'https://board.xcpcio.com/data/ccpc/12th/nanchang-invitational/run.json',
  // csgrandeur — hbcpc
  '/api/hbcpc/rank':      'https://cpc.csgrandeur.cn/upload/outrank_attach/88998553-ce66-46ec-a062-1beb357d774c/rank.json',
};

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js':   'application/javascript',
  '.css':  'text/css',
  '.json': 'application/json',
  '.png':  'image/png',
  '.ico':  'image/x-icon',
};

function proxyFetch(targetUrl, res) {
  https.get(targetUrl, {
    headers: {
      'User-Agent': 'Mozilla/5.0 (compatible; xcpcboard-proxy/1.0)',
      'Accept': 'application/json, */*',
    }
  }, (upstream) => {
    res.writeHead(upstream.statusCode, {
      'Content-Type': 'application/json; charset=utf-8',
      'Access-Control-Allow-Origin': '*',
      'Cache-Control': 'no-cache',
    });
    upstream.pipe(res);
  }).on('error', (err) => {
    res.writeHead(502, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: err.message }));
  });
}

const server = http.createServer((req, res) => {
  const parsedUrl = url.parse(req.url);
  const pathname = parsedUrl.pathname;

  // OPTIONS preflight
  if (req.method === 'OPTIONS') {
    res.writeHead(204, { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET' });
    res.end(); return;
  }

  // API proxy routes
  if (UPSTREAMS[pathname]) {
    proxyFetch(UPSTREAMS[pathname], res);
    return;
  }

  // Static file serving
  let filePath = path.join(__dirname, pathname === '/' ? 'index.html' : pathname);
  const ext = path.extname(filePath) || '.html';
  const contentType = MIME[ext] || 'application/octet-stream';

  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404); res.end('Not found'); return;
    }
    res.writeHead(200, { 'Content-Type': contentType });
    res.end(data);
  });
});

server.listen(PORT, () => {
  console.log(`\n  Board aggregator running at  http://localhost:${PORT}\n`);
});
