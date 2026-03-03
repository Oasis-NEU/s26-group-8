/**
 * Rate My Professor — Full Data Scraper
 * ======================================
 * Scrapes ALL professors at a school with every available data point.
 *
 * DATA COLLECTED PER PROFESSOR:
 *   - Name, Department, Overall Rating, Number of Ratings
 *   - Would Take Again %, Average Difficulty
 *   - Top Tags
 *   - All Reviews:
 *       Course, Quality, Difficulty, Date, Tags,
 *       For Credit, Attendance, Grade, Textbook, Comment
 *
 * Usage:
 *   node neu_scraper.js                    # Northeastern, profiles only
 *   node neu_scraper.js --reviews          # + all reviews (slow)
 *   node neu_scraper.js --reviews --csv    # + CSV export
 *   node neu_scraper.js --school mit       # Different school
 *   node neu_scraper.js --sid 696          # By school ID
 *   node neu_scraper.js --max-reviews 50   # More reviews per prof
 *   node neu_scraper.js --help
 *
 * Finding your school's SID:
 *   https://www.ratemyprofessors.com/search/teachers?query=*&sid=696
 *                                                              ^^^
 * Zero dependencies — Node.js 18+ only.
 */

const fs = require("fs");

// ─── Config ──────────────────────────────────────────────────────────────────

const BASE_URL = "https://www.ratemyprofessors.com/graphql";
const HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) " +
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
  Authorization: "Basic dGVzdDp0ZXN0",
  "Content-Type": "application/json",
  Referer: "https://www.ratemyprofessors.com/",
};
const REQUEST_DELAY = 400;

const KNOWN_SCHOOLS = {
  northeastern: { sid: 696, name: "Northeastern University" },
  mit: { sid: 580, name: "Massachusetts Institute of Technology" },
  harvard: { sid: 399, name: "Harvard University" },
  bu: { sid: 107, name: "Boston University" },
  bc: { sid: 103, name: "Boston College" },
  tufts: { sid: 1040, name: "Tufts University" },
  nyu: { sid: 675, name: "New York University" },
  stanford: { sid: 953, name: "Stanford University" },
  berkeley: { sid: 1072, name: "UC Berkeley" },
  ucla: { sid: 1075, name: "UCLA" },
  umass: { sid: 1513, name: "UMass Amherst" },
};

// ─── GraphQL Queries ─────────────────────────────────────────────────────────

const SEARCH_TEACHERS_QUERY = `
query TeacherSearchResultsPageQuery(
  $query: TeacherSearchQuery!
  $count: Int!
  $cursor: String
) {
  newSearch {
    teachers(query: $query, first: $count, after: $cursor) {
      didFallback
      edges {
        cursor
        node {
          id
          legacyId
          firstName
          lastName
          department
          school {
            id
            legacyId
            name
          }
          avgDifficulty
          avgRating
          numRatings
          wouldTakeAgainPercent
          teacherRatingTags {
            tagName
            tagCount
          }
        }
      }
      pageInfo {
        hasNextPage
        endCursor
      }
      resultCount
    }
  }
}`;

// Full ratings query — requests EVERY field that could exist on a rating
// GraphQL will simply ignore fields that don't exist in the schema
const RATINGS_QUERY = `
query RatingsListQuery(
  $id: ID!
  $count: Int!
  $cursor: String
) {
  node(id: $id) {
    ... on Teacher {
      id
      legacyId
      firstName
      lastName
      department
      school {
        id
        legacyId
        name
      }
      avgDifficulty
      avgRating
      numRatings
      wouldTakeAgainPercent
      teacherRatingTags {
        tagName
        tagCount
      }
      ratings(first: $count, after: $cursor) {
        edges {
          cursor
          node {
            id
            legacyId
            date
            class
            qualityRating
            difficultyRating
            comment
            helpfulRating
            notHelpfulRating
            grade
            wouldTakeAgain
            attendanceMandatory
            isForOnlineClass
            isForCredit
            textbookUse
            ratingTags
            flagStatus
          }
        }
        pageInfo {
          hasNextPage
          endCursor
        }
      }
    }
  }
}`;

// Fallback ratings query if the above fails (fewer fields)
const RATINGS_QUERY_FALLBACK = `
query RatingsListQuery(
  $id: ID!
  $count: Int!
  $cursor: String
) {
  node(id: $id) {
    ... on Teacher {
      id
      legacyId
      firstName
      lastName
      department
      school { id, legacyId, name }
      avgDifficulty
      avgRating
      numRatings
      wouldTakeAgainPercent
      teacherRatingTags { tagName, tagCount }
      ratings(first: $count, after: $cursor) {
        edges {
          cursor
          node {
            id
            date
            class
            qualityRating
            difficultyRating
            comment
            helpfulRating
            notHelpfulRating
            grade
            wouldTakeAgain
            attendanceMandatory
            isForOnlineClass
            ratingTags
          }
        }
        pageInfo {
          hasNextPage
          endCursor
        }
      }
    }
  }
}`;

// ─── Helpers ─────────────────────────────────────────────────────────────────

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function progress(current, total, label = "") {
  const w = 40;
  const pct = total > 0 ? current / total : 0;
  const filled = Math.round(w * pct);
  const bar = "█".repeat(filled) + "░".repeat(w - filled);
  process.stdout.write(
    `\r  [${bar}] ${(pct * 100).toFixed(1).padStart(5)}% (${current}/${total}) ${label}`
  );
}

async function gql(query, variables, retries = 3) {
  for (let attempt = 1; attempt <= retries; attempt++) {
    try {
      const resp = await fetch(BASE_URL, {
        method: "POST",
        headers: HEADERS,
        body: JSON.stringify({ query, variables }),
      });

      if (resp.status === 429) {
        const wait = attempt * 3000;
        console.log(`\n  [!] Rate limited — waiting ${wait / 1000}s...`);
        await sleep(wait);
        continue;
      }

      if (!resp.ok) {
        console.error(`\n  [!] HTTP ${resp.status}: ${resp.statusText}`);
        if (attempt < retries) await sleep(1000);
        continue;
      }

      const data = await resp.json();
      if (data.errors) {
        // Return errors alongside data so caller can decide
        return { data: data.data || {}, errors: data.errors };
      }
      return { data: data.data || {}, errors: null };
    } catch (err) {
      console.error(`\n  [!] Request failed (${attempt}/${retries}): ${err.message}`);
      if (attempt < retries) await sleep(1000 * attempt);
    }
  }
  return { data: {}, errors: [{ message: "All retries failed" }] };
}

// ─── Core Scraping ───────────────────────────────────────────────────────────

async function scrapeAllProfessors(sid, schoolName) {
  console.log(`\n  📥 Scraping all professors at ${schoolName} (SID: ${sid})...`);
  console.log("  This may take a few minutes.\n");

  const schoolId = Buffer.from(`School-${sid}`).toString("base64");
  const all = [];
  let cursor = null;
  let total = null;
  let page = 0;

  while (true) {
    const { data } = await gql(SEARCH_TEACHERS_QUERY, {
      query: { text: "", schoolID: schoolId, fallback: true, departmentID: null },
      count: 8,
      cursor,
    });

    const teachers = data?.newSearch?.teachers;
    if (!teachers) {
      if (page === 0) {
        console.log("  [!] No data returned. Schema may have changed.");
        console.log(`  [!] Check: https://www.ratemyprofessors.com/search/teachers?query=*&sid=${sid}`);
      }
      break;
    }

    if (total === null) {
      total = teachers.resultCount || 0;
      console.log(`  📊 Total professors found: ${total}\n`);
      if (total === 0) break;
    }

    const edges = teachers.edges || [];
    if (!edges.length) break;

    for (const { node: n } of edges) {
      if (n.school?.legacyId && n.school.legacyId !== sid) continue;

      const tags = (n.teacherRatingTags || [])
        .sort((a, b) => b.tagCount - a.tagCount)
        .slice(0, 10)
        .map((t) => ({ tag: t.tagName, count: t.tagCount }));

      all.push({
        // ── Professor Profile ──
        name: `${n.firstName || ""} ${n.lastName || ""}`.trim(),
        firstName: n.firstName || "",
        lastName: n.lastName || "",
        professorId: n.id,
        legacyId: n.legacyId,
        rmpUrl: n.legacyId
          ? `https://www.ratemyprofessors.com/professor/${n.legacyId}`
          : "",

        // ── Department & School ──
        department: n.department || "N/A",
        school: n.school?.name || schoolName,

        // ── Ratings Summary ──
        overallRating: n.avgRating || 0,
        numRatings: n.numRatings || 0,
        wouldTakeAgainPct: n.wouldTakeAgainPercent ?? -1,
        avgDifficulty: n.avgDifficulty || 0,

        // ── Tags ──
        tags,

        // ── Reviews (populated later with --reviews) ──
        reviews: [],
      });
    }

    page++;
    progress(Math.min(all.length, total), total, `(page ${page})`);

    const pi = teachers.pageInfo || {};
    if (!pi.hasNextPage) break;
    cursor = pi.endCursor;
    await sleep(REQUEST_DELAY);
  }

  console.log(`\n\n  ✅ Scraped ${all.length} professors.`);
  return all;
}

/**
 * Fetch all reviews for a single professor.
 * Tries the full query first, falls back to simpler query if fields don't exist.
 */
let useFullQuery = true; // start optimistic, fallback if needed

async function fetchReviews(professorId, maxReviews = 20) {
  const reviews = [];
  let cursor = null;
  const query = useFullQuery ? RATINGS_QUERY : RATINGS_QUERY_FALLBACK;

  while (reviews.length < maxReviews) {
    const { data, errors } = await gql(query, {
      id: professorId,
      count: Math.min(maxReviews - reviews.length, 20),
      cursor,
    });

    // If we get schema errors on the full query, switch to fallback
    if (errors && useFullQuery) {
      const schemaError = errors.some(
        (e) => e.message && e.message.includes("Cannot query field")
      );
      if (schemaError) {
        console.log("\n  [!] Some fields not in schema — switching to fallback query.");
        useFullQuery = false;
        // Retry with fallback
        return fetchReviews(professorId, maxReviews);
      }
    }

    const node = data?.node;
    if (!node?.ratings) break;

    for (const { node: r } of node.ratings.edges || []) {
      reviews.push({
        // ── Core Review Data ──
        course: r.class || "N/A",
        quality: r.qualityRating ?? null,
        difficulty: r.difficultyRating ?? null,
        date: r.date ? r.date.slice(0, 10) : "N/A",
        comment: r.comment || "",

        // ── Tags ──
        tags: r.ratingTags ? r.ratingTags.split("--").filter(Boolean) : [],

        // ── Additional Fields ──
        forCredit: r.isForCredit ?? null,
        attendance: r.attendanceMandatory ?? null,
        grade: r.grade || "N/A",
        textbook: r.textbookUse ?? null,
        wouldTakeAgain: r.wouldTakeAgain ?? null,
        isOnline: r.isForOnlineClass ?? null,

        // ── Helpfulness ──
        thumbsUp: r.helpfulRating || 0,
        thumbsDown: r.notHelpfulRating || 0,
      });
    }

    const pi = node.ratings.pageInfo || {};
    if (!pi.hasNextPage) break;
    cursor = pi.endCursor;
    await sleep(REQUEST_DELAY);
  }

  return reviews.slice(0, maxReviews);
}

async function enrichWithReviews(professors, maxReviews = 20) {
  const rated = professors.filter((p) => p.numRatings > 0);
  const unrated = professors.filter((p) => p.numRatings === 0);

  console.log(
    `\n  📝 Fetching reviews for ${rated.length} professors (max ${maxReviews} each)...`
  );
  console.log(`  (Skipping ${unrated.length} with 0 ratings)`);
  console.log("  ⚠️  This will take a while — be patient!\n");

  for (let i = 0; i < rated.length; i++) {
    const p = rated[i];
    progress(i + 1, rated.length, p.name.slice(0, 25).padEnd(25));

    try {
      p.reviews = await fetchReviews(p.professorId, maxReviews);
    } catch {
      p.reviews = [];
    }
    await sleep(REQUEST_DELAY);
  }

  unrated.forEach((p) => (p.reviews = []));
  console.log(`\n\n  ✅ Reviews fetched for ${rated.length} professors.`);
}

// ─── Export ──────────────────────────────────────────────────────────────────

function exportJson(professors, filename) {
  const output = {
    scraped_at: new Date().toISOString(),
    school: professors[0]?.school || "Unknown",
    total_professors: professors.length,
    total_with_ratings: professors.filter((p) => p.numRatings > 0).length,
    total_reviews: professors.reduce((s, p) => s + p.reviews.length, 0),
    professors,
  };
  fs.writeFileSync(filename, JSON.stringify(output, null, 2), "utf-8");
  console.log(`  💾 JSON: ${filename} (${(fs.statSync(filename).size / 1024 / 1024).toFixed(1)} MB)`);
}

function exportCsv(professors, filename) {
  // Professor-level CSV
  const esc = (s) => `"${String(s || "").replace(/"/g, '""')}"`;

  const profHeader = [
    "Name", "First Name", "Last Name", "Department", "School",
    "Overall Rating", "Num Ratings", "Would Take Again %", "Avg Difficulty",
    "Top Tags", "RMP URL",
  ].join(",");

  const profRows = professors.map((p) => [
    esc(p.name), esc(p.firstName), esc(p.lastName), esc(p.department), esc(p.school),
    p.overallRating.toFixed(1), p.numRatings,
    p.wouldTakeAgainPct >= 0 ? p.wouldTakeAgainPct.toFixed(1) : "N/A",
    p.avgDifficulty.toFixed(1),
    esc(p.tags.map((t) => t.tag).join("; ")),
    p.rmpUrl,
  ].join(","));

  const profFile = filename.replace(".csv", "_professors.csv");
  fs.writeFileSync(profFile, [profHeader, ...profRows].join("\n"), "utf-8");
  console.log(`  💾 CSV:  ${profFile}`);

  // Reviews CSV (if reviews were fetched)
  const hasReviews = professors.some((p) => p.reviews.length > 0);
  if (hasReviews) {
    const revHeader = [
      "Professor", "Department", "Course", "Quality", "Difficulty",
      "Date", "Grade", "For Credit", "Attendance", "Textbook",
      "Would Take Again", "Online", "Tags", "Comment",
      "Thumbs Up", "Thumbs Down",
    ].join(",");

    const revRows = [];
    for (const p of professors) {
      for (const r of p.reviews) {
        revRows.push([
          esc(p.name), esc(p.department), esc(r.course),
          r.quality, r.difficulty, r.date, esc(r.grade),
          r.forCredit ?? "", r.attendance ?? "", r.textbook ?? "",
          r.wouldTakeAgain ?? "", r.isOnline ?? "",
          esc(r.tags.join("; ")), esc(r.comment),
          r.thumbsUp, r.thumbsDown,
        ].join(","));
      }
    }

    const revFile = filename.replace(".csv", "_reviews.csv");
    fs.writeFileSync(revFile, [revHeader, ...revRows].join("\n"), "utf-8");
    console.log(`  💾 CSV:  ${revFile} (${revRows.length} reviews)`);
  }
}

function printSummary(professors) {
  const rated = professors.filter((p) => p.numRatings > 0);
  const avg = (arr, fn) =>
    arr.length ? arr.reduce((s, p) => s + fn(p), 0) / arr.length : 0;

  const depts = {};
  professors.forEach((p) => { depts[p.department] = (depts[p.department] || 0) + 1; });
  const topDepts = Object.entries(depts).sort((a, b) => b[1] - a[1]).slice(0, 10);

  const min = 5;
  const top = [...rated].filter((p) => p.numRatings >= min)
    .sort((a, b) => b.overallRating - a.overallRating).slice(0, 10);
  const bottom = [...rated].filter((p) => p.numRatings >= min)
    .sort((a, b) => a.overallRating - b.overallRating).slice(0, 10);

  const totalReviews = professors.reduce((s, p) => s + p.reviews.length, 0);

  console.log(`\n${"=".repeat(60)}`);
  console.log("  📊 SUMMARY");
  console.log(`${"=".repeat(60)}`);
  console.log(`  Total professors:      ${professors.length}`);
  console.log(`  With ratings:          ${rated.length}`);
  console.log(`  Without ratings:       ${professors.length - rated.length}`);
  if (totalReviews > 0) console.log(`  Total reviews scraped: ${totalReviews}`);
  console.log(`  Avg overall rating:    ${avg(rated, (p) => p.overallRating).toFixed(2)} / 5.0`);
  console.log(`  Avg difficulty:        ${avg(rated, (p) => p.avgDifficulty).toFixed(2)} / 5.0`);

  const wtaProfs = rated.filter((p) => p.wouldTakeAgainPct >= 0);
  if (wtaProfs.length) {
    console.log(`  Avg would take again:  ${avg(wtaProfs, (p) => p.wouldTakeAgainPct).toFixed(1)}%`);
  }

  if (topDepts.length) {
    console.log(`\n  🏛️  Top Departments:`);
    topDepts.forEach(([d, c]) => console.log(`    ${String(c).padStart(4)} — ${d}`));
  }
  if (top.length) {
    console.log(`\n  ⭐ Top 10 Rated (min ${min} ratings):`);
    top.forEach((p, i) => console.log(
      `    ${String(i + 1).padStart(2)}. ${p.overallRating.toFixed(1)} — ${p.name} (${p.department}, ${p.numRatings} ratings)`
    ));
  }
  if (bottom.length) {
    console.log(`\n  📉 Bottom 10 Rated (min ${min} ratings):`);
    bottom.forEach((p, i) => console.log(
      `    ${String(i + 1).padStart(2)}. ${p.overallRating.toFixed(1)} — ${p.name} (${p.department}, ${p.numRatings} ratings)`
    ));
  }
  console.log(`\n${"=".repeat(60)}\n`);
}

// ─── CLI ─────────────────────────────────────────────────────────────────────

function parseArgs() {
  const a = { sid: 696, schoolName: "Northeastern University", reviews: false, maxReviews: 20, output: null, csv: false };
  const argv = process.argv.slice(2);

  for (let i = 0; i < argv.length; i++) {
    switch (argv[i]) {
      case "--sid": a.sid = parseInt(argv[++i]); a.schoolName = `School SID ${a.sid}`; break;
      case "--school": {
        const k = argv[++i]?.toLowerCase();
        if (KNOWN_SCHOOLS[k]) { a.sid = KNOWN_SCHOOLS[k].sid; a.schoolName = KNOWN_SCHOOLS[k].name; }
        else { console.log(`Unknown: "${k}". Known: ${Object.keys(KNOWN_SCHOOLS).join(", ")}`); process.exit(1); }
        break;
      }
      case "--reviews": a.reviews = true; break;
      case "--max-reviews": a.maxReviews = parseInt(argv[++i]); break;
      case "--output": a.output = argv[++i]; break;
      case "--csv": a.csv = true; break;
      case "--help":
        console.log(`
  Rate My Professor — Full Data Scraper

  Usage: node neu_scraper.js [options]

  Options:
    --sid <number>         School ID (default: 696 = Northeastern)
    --school <shortcut>    Known school: ${Object.keys(KNOWN_SCHOOLS).join(", ")}
    --reviews              Fetch all individual reviews (slow but comprehensive)
    --max-reviews <n>      Reviews per professor (default: 20)
    --output <file>        JSON output filename
    --csv                  Also export CSV files

  Data collected per professor:
    Name, Department, Rating, # Ratings, Would Take Again %, Difficulty, Tags

  Data collected per review (with --reviews):
    Course, Quality, Difficulty, Date, Tags, For Credit,
    Attendance, Grade, Textbook, Comment, Would Take Again

  Output files:
    {school}_professors.json         — Full JSON data
    {school}_professors.csv          — Professor summary
    {school}_reviews.csv             — All reviews (with --reviews --csv)
`);
        process.exit(0);
    }
  }
  return a;
}

async function main() {
  const args = parseArgs();

  console.log(`\n${"=".repeat(60)}`);
  console.log("  🎓 Rate My Professor — Full Data Scraper");
  console.log(`${"=".repeat(60)}`);
  console.log(`  School:  ${args.schoolName}`);
  console.log(`  SID:     ${args.sid}`);
  console.log(`  Reviews: ${args.reviews ? `Yes (max ${args.maxReviews} per prof)` : "No (use --reviews to enable)"}`);

  // Step 1: Get all professors
  const profs = await scrapeAllProfessors(args.sid, args.schoolName);
  if (!profs.length) { console.log("\n  No professors found."); return; }

  // Step 2: Get reviews
  if (args.reviews) {
    await enrichWithReviews(profs, args.maxReviews);
  }

  // Step 3: Summary
  printSummary(profs);

  // Step 4: Export
  const safe = args.schoolName.replace(/[^a-zA-Z0-9]/g, "_").toLowerCase();
  const jsonFile = args.output || `${safe}_professors.json`;
  exportJson(profs, jsonFile);
  if (args.csv) exportCsv(profs, jsonFile.replace(".json", ".csv"));

  console.log("\n  🎉 Done!\n");
}

main().catch(console.error);