// Build-time sitemap generator.
//
// Pulls every professor slug and course code from the live API and writes
// public/sitemap.xml so crawlers (and AI engines) can discover every page.
// Runs as part of `npm run build` (see package.json prebuild). It degrades
// gracefully: if the API is unreachable it still writes a sitemap with the
// static routes so the build never fails on a transient network blip.

import { writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT = join(__dirname, '..', 'public', 'sitemap.xml');

const SITE = process.env.SITE_URL || 'https://ratemyhusky.com';
const API = process.env.SITEMAP_API_URL || 'https://ratemyhusky.com';

// Static, always-present routes. `priority` is a hint, not a ranking lever.
const STATIC_ROUTES = [
  { path: '/', priority: '1.0', changefreq: 'daily' },
  { path: '/professors', priority: '0.9', changefreq: 'daily' },
  { path: '/courses', priority: '0.9', changefreq: 'daily' },
  { path: '/compare', priority: '0.5', changefreq: 'weekly' },
];

async function fetchAll(endpoint, key) {
  // Catalog endpoints accept a large limit and return { [key]: [...] }.
  const url = `${API}/api/${endpoint}?limit=10000&page=1`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${endpoint} -> HTTP ${res.status}`);
  const data = await res.json();
  return Array.isArray(data[key]) ? data[key] : [];
}

function xmlUrl({ loc, changefreq, priority, lastmod }) {
  return [
    '  <url>',
    `    <loc>${loc}</loc>`,
    lastmod ? `    <lastmod>${lastmod}</lastmod>` : null,
    changefreq ? `    <changefreq>${changefreq}</changefreq>` : null,
    priority ? `    <priority>${priority}</priority>` : null,
    '  </url>',
  ].filter(Boolean).join('\n');
}

function esc(s) {
  return String(s).replace(/[<>&'"]/g, (c) =>
    ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', "'": '&apos;', '"': '&quot;' }[c]));
}

async function main() {
  const today = new Date().toISOString().slice(0, 10);
  const urls = STATIC_ROUTES.map((r) =>
    xmlUrl({ loc: `${SITE}${r.path}`, changefreq: r.changefreq, priority: r.priority, lastmod: today }));

  let profCount = 0;
  let courseCount = 0;

  try {
    const professors = await fetchAll('professors-catalog', 'professors');
    for (const p of professors) {
      if (!p.slug) continue;
      urls.push(xmlUrl({ loc: `${SITE}/professors/${esc(p.slug)}`, changefreq: 'weekly', priority: '0.7', lastmod: today }));
      profCount++;
    }
  } catch (e) {
    console.warn(`[sitemap] could not fetch professors: ${e.message}`);
  }

  try {
    const courses = await fetchAll('courses-catalog', 'courses');
    for (const c of courses) {
      if (!c.code) continue;
      urls.push(xmlUrl({ loc: `${SITE}/courses/${esc(c.code)}`, changefreq: 'weekly', priority: '0.6', lastmod: today }));
      courseCount++;
    }
  } catch (e) {
    console.warn(`[sitemap] could not fetch courses: ${e.message}`);
  }

  const xml =
    '<?xml version="1.0" encoding="UTF-8"?>\n' +
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' +
    urls.join('\n') +
    '\n</urlset>\n';

  await writeFile(OUT, xml, 'utf8');
  console.log(`[sitemap] wrote ${urls.length} urls (${profCount} professors, ${courseCount} courses) -> public/sitemap.xml`);
}

main().catch((e) => {
  // Never fail the build over the sitemap.
  console.warn(`[sitemap] generation failed: ${e.message}`);
});
