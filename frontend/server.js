#!/usr/bin/env node
const http = require('http');
const fs = require('fs');
const path = require('path');

const port = Number(process.env.PORT || 8080);
const root = path.join(__dirname, 'dist');
const types = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.xml': 'application/xml; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
};

function safePath(urlPath) {
  const decoded = decodeURIComponent((urlPath || '/').split('?')[0]);
  const normalized = path.posix.normalize(decoded).replace(/^\/+/, '');
  const candidate = path.join(root, normalized);
  return candidate.startsWith(root) ? candidate : null;
}

function resolveFile(urlPath) {
  const candidate = safePath(urlPath);
  if (!candidate) return null;
  try {
    if (fs.statSync(candidate).isDirectory()) {
      const index = path.join(candidate, 'index.html');
      if (fs.existsSync(index)) return index;
    }
    if (fs.statSync(candidate).isFile()) return candidate;
  } catch (_) {}
  if (!path.extname(candidate)) {
    const html = `${candidate}.html`;
    if (fs.existsSync(html)) return html;
  }
  return null;
}

const server = http.createServer((req, res) => {
  const file = resolveFile(req.url);
  if (!file) {
    const notFound = path.join(root, '404.html');
    res.writeHead(404, {'Content-Type': 'text/html; charset=utf-8'});
    return fs.createReadStream(notFound).pipe(res);
  }
  const ext = path.extname(file).toLowerCase();
  const headers = {
    'Content-Type': types[ext] || 'application/octet-stream',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'strict-origin-when-cross-origin',
  };
  if (file.endsWith('.html')) headers['Cache-Control'] = 'no-cache';
  else headers['Cache-Control'] = 'public, max-age=31536000, immutable';
  res.writeHead(200, headers);
  fs.createReadStream(file).pipe(res);
});

server.listen(port, '0.0.0.0', () => {
  console.log(`HaulChime frontend listening on 0.0.0.0:${port}`);
});
