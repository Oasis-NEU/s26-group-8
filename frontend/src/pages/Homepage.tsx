/*
Primary Homepage Codespace
*/
import { useState, useEffect, useRef, useCallback, useLayoutEffect } from 'react';
import { useNavigate, useLocation, Link } from 'react-router-dom';
import SearchBar from '../components/SearchBar';
import Footer from '../components/Footer';
import Seo from '../components/Seo';
import { fetchGoatProfessors, fetchProfessorsCatalog } from '../api/api';
import type { CatalogProfessor, Professor } from '../api/api';
import neuIcon from '../assets/neu-circle-icon.png';
import { ASK_ENABLED } from '../config';
import './Homepage.css';

const STATS = [
  { label: 'Professors', value: '9,300+' },
  { label: 'Courses', value: '7,900+' },
  { label: 'Comments', value: '1,767,900+' },
  { label: 'Departments', value: '80' },
];

const COLLEGES = [
  'Business', 'CAMD', 'CSSH', 'Engineering',
  'Health Sciences', 'Khoury', 'Law', 'Professional Studies', 'Science',
];

// Module-level caches so data survives component unmounts
const goatCache = new Map<string, Professor[]>();
let wheelPool: CatalogProfessor[] = [];
let wheelPoolLoaded = false;

/* ---- animated stat counter ---- */
const AnimatedStat = ({ value, label }: { value: string; label: string }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const valueRef = useRef<HTMLSpanElement>(null);
  const hasAnimated = useRef(false);

  // Parse "7,600+" → { num: 7600, suffix: "+" }
  const parsed = useRef({ num: 0, suffix: '' });
  useEffect(() => {
    const clean = value.replace(/,/g, '');
    const match = clean.match(/^(\d+)(.*)$/);
    if (match) {
      parsed.current = { num: parseInt(match[1], 10), suffix: match[2] };
    }
  }, [value]);

  useEffect(() => {
    const container = containerRef.current;
    const el = valueRef.current;
    if (!container || !el) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && !hasAnimated.current) {
          hasAnimated.current = true;
          observer.disconnect();

          const { num, suffix } = parsed.current;
          const duration = 2000;
          const start = performance.now();

          const step = (now: number) => {
            const progress = Math.min((now - start) / duration, 1);
            const eased = 1 - Math.pow(1 - progress, 3);
            el.textContent = Math.round(eased * num).toLocaleString() + suffix;
            if (progress < 1) requestAnimationFrame(step);
          };
          requestAnimationFrame(step);
        }
      },
      { threshold: 0.5 }
    );
    observer.observe(container);
    return () => observer.disconnect();
  }, [value]);

  return (
    <div className="stat-item" ref={containerRef}>
      <span className="stat-value" ref={valueRef}>0</span>
      <span className="stat-label">{label}</span>
    </div>
  );
};

/* ---- partial star renderer ---- */
const Stars = ({ rating }: { rating: number }) => {
  // Width percentage: e.g. rating 4.3 → 86% of 5 stars
  const pct = (Math.min(Math.max(rating, 0), 5) / 5) * 100;
  return (
    <span className="stars-wrapper">
      <span className="stars-empty">★★★★★</span>
      <span className="stars-filled" style={{ width: `${pct}%` }}>★★★★★</span>
    </span>
  );
};

/* ---- how the board is ordered ----
   The board sorts on the rating shrunk toward the site average by how many
   ratings back it, while the Rating column shows the unshrunk average — so that
   column legitimately moves backwards between adjacent rows, 2-4 times per board
   on every board.

   Behind a "?" beside the heading rather than always on. What keeps the board
   from going unexplained without it is that both Rating and Ratings carry
   .goat-col-ranked, so the columns themselves still say two things feed the
   sort; the tooltip is where the "why" lives, not the only place the fact
   appears.

   Three short sentences, each doing one job: what we rank on, which way the
   trade runs, and what the reader will therefore see. Two attempts failed before
   this. "Ranked by rating together with how many ratings back it" named the
   mechanism and left the direction unstated. Adding "a near-perfect score from a
   handful will not outrank a strong one from hundreds" fixed the direction but
   packed the whole thing into two long clauses hinged on a dash, so the sentence
   had to be re-read to be parsed.

   Plain words over precise ones where they conflict: "how many students rated
   them" rather than ratings or responses, "counts for less" rather than shrunk or
   weighted, "highest to lowest" rather than descending. The reader this is for is
   picking a class, not auditing an estimator.

   Still no worked example. Every concrete pair has a prior at which it inverts
   ("5.0 from 8 loses to 4.8 from 500" flips once the prior passes 4.76, and the
   prior is re-measured from the corpus on every run, currently 4.4525), so a
   number here is a sentence a future re-scrape can quietly turn into a lie. The
   comparative version holds whatever the corpus does, because shrinkage always
   pulls a thin sample toward the prior. */
const RANK_NOTE =
  'Professors are ranked by their rating and by how many students rated them. ' +
  'A very high score from only a few students counts for less than a slightly ' +
  'lower score from hundreds. That is why the ratings below do not always go ' +
  'from highest to lowest.';

/* ---- rating cell with hover/click tooltip ---- */
const RatingCell = ({ prof, isOpen, onToggle }: {
  prof: Professor;
  isOpen: boolean;
  onToggle: () => void;
}) => {
  const ref = useRef<HTMLSpanElement>(null);

  // Close on outside click
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onToggle();
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [isOpen, onToggle]);

  return (
    <span
      ref={ref}
      className="goat-col-rating goat-rating-wrapper"
      onClick={(e) => { e.stopPropagation(); onToggle(); }}
    >
      <span className="goat-score">{prof.avgRating?.toFixed(2) ?? '—'}</span>
      <Stars rating={prof.avgRating ?? 0} />
      <span className="goat-rating-hint">ⓘ</span>

      {isOpen && (
        <div className="goat-rating-tooltip">
          <div className="tooltip-row">
            <span className="tooltip-label">RMP</span>
            <span className="tooltip-value">
              {prof.rmpRating !== null ? prof.rmpRating.toFixed(2) : '—'}
            </span>
          </div>
          {/* The number the blend was actually computed from. Without it the
              panel listed three figures in two unit systems and invited an
              arithmetic that cannot work: RMP 5.00 with TRACE 5.00 showed an Avg
              of 4.99, because RMP 5.00 is 4.96 once projected. With it, Avg
              always lies between the two values above — checked against all
              1,708 two-source professors, and inherent to pooling, which cannot
              leave the interval its inputs span.

              Indented under RMP rather than given a row of its own, because it
              is the same measurement in different units, not a third source. */}
          {prof.rmpAdjusted != null && (
            <div className="tooltip-row tooltip-row-sub">
              <span className="tooltip-label">on the TRACE scale</span>
              <span className="tooltip-value">{prof.rmpAdjusted.toFixed(2)}</span>
            </div>
          )}
          <div className="tooltip-row">
            <span className="tooltip-label">TRACE</span>
            <span className="tooltip-value">
              {prof.traceRating !== null ? prof.traceRating.toFixed(2) : '—'}
            </span>
          </div>
          <div className="tooltip-divider" />
          <div className="tooltip-row">
            <span className="tooltip-label">Avg Rating</span>
            <span className="tooltip-value tooltip-blended">{prof.avgRating?.toFixed(2) ?? '—'}</span>
          </div>
          {/* Why Avg Rating is not simply one of the numbers above it. Both cases
              need saying, and for the same underlying reason: the column is on the
              TRACE scale, which runs about 0.8 higher than RMP's and is 2.4x
              narrower.

              Written as two plain statements of what happens, in order, naming
              RateMyProfessors in full once so "RMP" in the row above is anchored
              for a reader who has not met the abbreviation. Earlier drafts said
              "RMP's scale runs lower, so it converts first", which assumes the
              reader already pictures two scales, and "leaning on whichever has
              more responses behind it", where "leaning" and "behind it" both ask
              to be decoded.

              What the note deliberately does not do is name weights a reader can
              try. It used to say "weighted by the ratings behind each", and those
              weights do not work: pooling is inverse-variance, so one RMP rating
              carries ~1.88x the weight of one TRACE response, and the
              per-response variances behind that factor are measured from raw
              review rows the catalog does not store. The exact sum is not
              reproducible from anything on this page, so printing the two counts
              would only make a false promise look better evidenced. "Counts for
              more" is the part that is both true and checkable by eye against the
              two numbers above.

              With RMP alone, Avg no longer equals the RMP row at all. That is the
              visible cost of putting every professor in the column on one scale,
              and an unexplained 3.10 turning into 4.11 is exactly what reads as a
              bug, so that case says outright that there is no TRACE score. */}
          {prof.rmpRating !== null && prof.traceRating !== null ? (
            <div className="tooltip-note">
              RateMyProfessors scores run lower than TRACE scores, so the RMP score
              is converted to the TRACE scale first. The two are then averaged, and
              the one with more responses counts for more.
            </div>
          ) : prof.rmpRating !== null ? (
            <div className="tooltip-note">
              This professor has no TRACE scores, so the RMP score is converted to
              the TRACE scale to keep it comparable.
            </div>
          ) : null}
        </div>
      )}
    </span>
  );
};

const Homepage = () => {
  const navigate = useNavigate();
  const location = useLocation();

  // Disable browser scroll restoration, scroll to top on refresh
  useEffect(() => {
    if ('scrollRestoration' in history) {
      history.scrollRestoration = 'manual';
    }
    if (location.hash && location.state) {
      const el = document.getElementById(location.hash.slice(1));
      if (el) {
        setTimeout(() => el.scrollIntoView({ behavior: 'smooth' }), 100);
      }
    } else {
      window.scrollTo(0, 0);
    }
  }, [location.hash, location.state]);
  const [selectedCollege, setSelectedCollege] = useState<string>(() => {
    const state = location.state as { goatedCollege?: string } | null;
    const restored = state?.goatedCollege;
    return restored && COLLEGES.includes(restored) ? restored : COLLEGES[0];
  });
  const [profs, setProfs] = useState<Professor[]>([]);
  const [profsLoading, setProfsLoading] = useState(false);
  const [goatVisible, setGoatVisible] = useState(false);
  const goatSectionRef = useRef<HTMLElement>(null);
  const [shuffleVisible, setShuffleVisible] = useState(false);
  const shuffleSectionRef = useRef<HTMLElement>(null);
  const [shuffling, setShuffling] = useState(false);
  const [openTooltip, setOpenTooltip] = useState<number | null>(null);
  const [showAskTip, setShowAskTip] = useState(() => localStorage.getItem('home_ask_tip_dismissed') !== '1');
  const [askTrigger, setAskTrigger] = useState<number | undefined>(undefined);
  const tabsRef = useRef<HTMLDivElement>(null);
  const [tabsAtEnd, setTabsAtEnd] = useState(false);
  const [tabsAtStart, setTabsAtStart] = useState(true);
  const leaderboardRef = useRef<HTMLDivElement>(null);
  const [leaderFade, setLeaderFade] = useState({ left: false, right: false });

  const updateLeaderFade = useCallback(() => {
    const el = leaderboardRef.current;
    if (!el) return;
    setLeaderFade({
      left: el.scrollLeft > 0,
      right: el.scrollLeft + el.clientWidth < el.scrollWidth - 1,
    });
  }, []);

  useEffect(() => {
    const el = leaderboardRef.current;
    if (!el) return;
    updateLeaderFade();
    el.addEventListener('scroll', updateLeaderFade, { passive: true });
    return () => el.removeEventListener('scroll', updateLeaderFade);
  }, [profs, updateLeaderFade]);

  // Pill animation state
  const [pillStyle, setPillStyle] = useState({ left: 0, width: 0, opacity: 0 });
  const [isPillReady, setIsPillReady] = useState(false);

  // Navigate to professor page
  const handleProfClick = (name: string) => {
    const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    navigate(`/professors/${slug}`, {
      state: { fromPage: { label: 'GOATED Professors', url: '/#goated' }, goatedCollege: selectedCollege },
    });
  };

  const updatePill = useCallback(() => {
    if (!tabsRef.current) return;
    const activeTab = tabsRef.current.querySelector('.goat-tab.active') as HTMLElement;
    if (activeTab) {
      setPillStyle({
        left: activeTab.offsetLeft,
        width: activeTab.offsetWidth,
        opacity: 1
      });
    }
  }, []);

  // Update pill on selection change
  useLayoutEffect(() => {
    updatePill();
  }, [selectedCollege, updatePill]);

  // Handle initialization and resize via ResizeObserver
  useEffect(() => {
    const container = tabsRef.current;
    if (!container) return;

    updatePill();
    const readyTimer = setTimeout(() => setIsPillReady(true), 150);

    const observer = new ResizeObserver(() => {
      setIsPillReady(false);
      updatePill();
      setTimeout(() => setIsPillReady(true), 50);
    });

    observer.observe(container);
    return () => {
      clearTimeout(readyTimer);
      observer.disconnect();
    };
  }, [updatePill]);

  // Detect scroll position on college tabs
  useEffect(() => {
    const el = tabsRef.current;
    if (!el) return;

    const checkScroll = () => {
      setTabsAtStart(el.scrollLeft <= 10);
      setTabsAtEnd(el.scrollLeft + el.clientWidth >= el.scrollWidth - 10);
    };

    checkScroll();
    el.addEventListener('scroll', checkScroll);
    window.addEventListener('resize', checkScroll);
    return () => {
      el.removeEventListener('scroll', checkScroll);
      window.removeEventListener('resize', checkScroll);
    };
  }, []);

  // Trigger fetch when goat section scrolls into view
  useEffect(() => {
    const el = goatSectionRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setGoatVisible(true);
          observer.disconnect();
        }
      },
      { threshold: 0 }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // goatCache is defined at module level so it survives unmounts

  // Load GOAT professors when section is visible and selected college changes
  useEffect(() => {
    if (!goatVisible || !selectedCollege) return;

    const cached = goatCache.get(selectedCollege);
    if (cached) {
      setProfs(cached);
      setOpenTooltip(null);
      return;
    }

    let cancelled = false;

    async function loadProfs() {
      setProfsLoading(true);
      setOpenTooltip(null);
      try {
        const data = await fetchGoatProfessors(selectedCollege);
        if (!cancelled) {
          goatCache.set(selectedCollege, data);
          setProfs(data);
        }
      } catch (err) {
        console.error('Failed to load professors:', err);
      } finally {
        if (!cancelled) setProfsLoading(false);
      }
    }
    loadProfs();

    return () => { cancelled = true; };
  }, [goatVisible, selectedCollege]);


  const [slotResult, setSlotResult] = useState<{ name: string; dept: string; college: string; slug: string } | null>(null);
  const [wheelState, setWheelState] = useState<'idle' | 'spinning' | 'result'>('idle');
  const WHEEL_SLICES = 16;
  const SLICE_DEG = 360 / WHEEL_SLICES;
  const [wheelNames, setWheelNames] = useState<string[]>(Array.from({ length: WHEEL_SLICES }, () => ''));
  const [wheelRotation, setWheelRotation] = useState(0);
  const [wheelDurationMs, setWheelDurationMs] = useState(0);

  // wheelPool is defined at module level so it survives unmounts

  useEffect(() => {
    const el = shuffleSectionRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setShuffleVisible(true);
          observer.disconnect();
        }
      },
      { threshold: 0 }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!shuffleVisible || wheelPoolLoaded) return;
    wheelPoolLoaded = true;
    fetchProfessorsCatalog({ minRating: 3, limit: 100, sort: 'rating' })
      .then((res) => { wheelPool = res.professors; })
      .catch((err) => console.error('Failed to load wheel pool:', err));
  }, [shuffleVisible]);

  const handleShuffle = async () => {
    if (shuffling) return;
    const pool = wheelPool;
    if (pool.length < WHEEL_SLICES) {
      console.error('Not enough professors in wheel pool');
      return;
    }

    setShuffling(true);
    setSlotResult(null);
    setWheelState('spinning');

    try {
      // Pick a random winner from the pool
      const shuffledPool = [...pool].sort(() => Math.random() - 0.5);
      const winner = shuffledPool[0];
      const sliceProfs = shuffledPool.slice(0, WHEEL_SLICES);
      const names = sliceProfs.map((p) => p.name);

      const winnerIndex = 0; // winner is first after shuffle
      setWheelNames(names);

      const slug = winner.slug || winner.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

      const currentNormalized = ((wheelRotation % 360) + 360) % 360;
      const winnerCenterAngle = (winnerIndex + 0.5) * SLICE_DEG;
      const pointerAngle = 0;
      const targetNormalized = (pointerAngle - winnerCenterAngle + 360) % 360;
      const delta = (targetNormalized - currentNormalized + 360) % 360;
      const extraSpins = 360 * 6;
      const finalRotation = wheelRotation + extraSpins + delta;

      setWheelDurationMs(0);
      await new Promise<void>(r => requestAnimationFrame(() => requestAnimationFrame(() => r())));
      setWheelDurationMs(4800);
      setWheelRotation(finalRotation);

      await new Promise(r => setTimeout(r, 5000));

      setSlotResult({ name: winner.name, dept: winner.department ?? '', college: winner.college ?? '', slug });
      setWheelState('result');
    } catch (err) {
      console.error('Failed to spin wheel:', err);
      setWheelState('idle');
    } finally {
      setShuffling(false);
    }
  };

  return (
    <div className="homepage">
      <Seo
        title="RateMyHusky — Northeastern University Professor Reviews & Ratings"
        description="Find the right Northeastern professor every semester. RateMyHusky combines TRACE evaluations and RateMyProfessor ratings and reviews in one place."
        canonical="https://ratemyhusky.com/"
        jsonLd={{
          '@context': 'https://schema.org',
          '@type': 'WebSite',
          name: 'RateMyHusky',
          url: 'https://ratemyhusky.com/',
          description: 'Northeastern University professor and course ratings combining TRACE evaluations and RateMyProfessor reviews.',
        }}
      />

      {ASK_ENABLED && showAskTip && (
        <div className="home-ask-bubble" role="status">
          <div className="home-ask-bubble-icon">✨</div>
          <div className="home-ask-bubble-body">
            <div className="home-ask-bubble-label">New</div>
            <p className="home-ask-bubble-text">
              Get AI-powered answers about professors and courses.{' '}
              <button
                type="button"
                className="home-ask-bubble-trynow"
                onClick={() => {
                  setAskTrigger(Date.now());
                  localStorage.setItem('home_ask_tip_dismissed', '1');
                  setShowAskTip(false);
                }}
              >
                Click here to Try Now
              </button>
            </p>
          </div>
          <button
            className="home-ask-bubble-close"
            onClick={() => { localStorage.setItem('home_ask_tip_dismissed', '1'); setShowAskTip(false); }}
            aria-label="Dismiss update"
          >
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      )}

      {/* ======== Hero ======== */}
      <main className="homepage-hero">
        <div
          className="hero-bg-pattern"
          style={{ backgroundImage: `url(${neuIcon})` }}
        />
        <h1 className="hero-tagline">
          Find the <span>right professor</span>, every semester
        </h1>
        <p className="hero-subtitle">
          TRACE evaluations and RateMyProfessor ratings, all in one place.
        </p>

        <SearchBar forceAsk={askTrigger} restoreAsk={(location.state as { restoreAsk?: boolean } | null)?.restoreAsk} />
      </main>

      {/* ======== Stats Banner ======== */}
      <section className="stats-banner">
        {STATS.map((s) => (
          <AnimatedStat key={s.label} value={s.value} label={s.label} />
        ))}
      </section>

      {/* ======== GOAT Professors Leaderboard ======== */}
      <section id="goated" className="section goat-section" ref={goatSectionRef}>
        <div className="section-header">
          {/* Wrapper, not a bare sibling: .section-header is space-between, so an
              unwrapped icon would fly to the far right instead of sitting beside
              the heading. Outside the h2 so it stays out of the accessible name. */}
          <div className="goat-title-row">
            <h2 className="section-title">GOATED Professors</h2>
            {/* A real button, not a styled span, so the tooltip is reachable
                without a pointer: it opens on :hover for mice and :focus-visible
                for keyboards, and on a touch device a tap fires the sticky
                :hover. All three paths are CSS, so there is no open/closed state
                to track here — unlike RatingCell, which needs click-toggling
                because its tooltip sits inside a row that is itself a link. */}
            <button
              type="button"
              className="goat-rank-help"
              aria-label="How the ranking works"
              aria-describedby="goat-rank-note"
            >
              ?
              <span id="goat-rank-note" role="tooltip" className="goat-rank-tooltip">
                {RANK_NOTE}
              </span>
            </button>
          </div>
        </div>

        <div
          className={`goat-college-tabs${tabsAtStart ? ' scrolled-start' : ''}${tabsAtEnd ? ' scrolled-end' : ''}`}
          ref={tabsRef}
        >
          <div 
            className={`goat-pill-background ${isPillReady ? 'animate' : ''}`}
            style={{
              transform: `translateX(${pillStyle.left}px)`,
              width: `${pillStyle.width}px`,
              opacity: pillStyle.opacity,
              visibility: pillStyle.opacity === 0 ? 'hidden' : 'visible'
            }}
          />
          {COLLEGES.map((c) => (
            <button
              key={c}
              className={`goat-tab ${c === selectedCollege ? 'active' : ''}`}
              onClick={(e) => {
                setSelectedCollege(c);
                const container = tabsRef.current;
                const btn = e.currentTarget;
                if (container) {
                  const fadeWidth = 40;
                  const targetScroll = Math.max(0, btn.offsetLeft - fadeWidth);
                  const maxScroll = container.scrollWidth - container.clientWidth;
                  container.scrollTo({
                    left: Math.min(targetScroll, maxScroll),
                    behavior: 'smooth',
                  });
                }
              }}
            >
              {c}
            </button>
          ))}
        </div>

        <div className={`goat-scroll-wrap${leaderFade.left ? ' fade-left' : ''}${leaderFade.right ? ' fade-right' : ''}`}>
        <div ref={leaderboardRef} className="goat-leaderboard">
          <div className="goat-header-row">
            <span className="goat-col-rank">#</span>
            <span className="goat-col-name">Professor</span>
            <span className="goat-col-dept">Department</span>
            {/* Both of these feed the sort, so both are marked. Marking only
                Rating would restate the wrong mental model the note above exists
                to correct — which is also why there is no sort caret here: a "↓"
                on Rating alone reads as "sorted by Rating, descending", the exact
                claim the note is there to deny. */}
            <span className="goat-col-rating goat-col-ranked">Rating</span>
            {/* "Ratings", not "Reviews": the number is RMP ratings plus TRACE
                survey responses (~95% the latter), and 1,907 of the professors
                eligible for these boards have no written RMP review at all. */}
            <span className="goat-col-reviews goat-col-ranked">Ratings</span>
          </div>

          {profsLoading ? (
            Array.from({ length: 10 }).map((_, i) => (
              <div key={i} className="goat-row goat-skeleton-row">
                <span className="goat-col-rank"><span className="skeleton-bone skeleton-rank" /></span>
                <div className="goat-col-name"><span className="skeleton-bone skeleton-name" /></div>
                <span className="goat-col-dept"><span className="skeleton-bone skeleton-dept" /></span>
                <span className="goat-col-rating"><span className="skeleton-bone skeleton-rating" /></span>
                <span className="goat-col-reviews"><span className="skeleton-bone skeleton-reviews" /></span>
              </div>
            ))
          ) : profs.length === 0 ? (
            <div className="goat-row" style={{ justifyContent: 'center', opacity: 0.6 }}>
              No professors found for this college.
            </div>
          ) : (
            profs.map((p, i) => (
              <div
                key={p.name}
                className={`goat-row ${i < 3 ? 'goat-top3' : ''}`}
                onClick={() => handleProfClick(p.name)}
              >
                <span className="goat-col-rank">
                  {i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : i + 1}
                </span>
                <div className="goat-col-name">
                  <span className="goat-name-text">{p.name}</span>
                </div>
                <span className="goat-col-dept">{p.dept}</span>
                <RatingCell
                  prof={p}
                  isOpen={openTooltip === i}
                  onToggle={() => setOpenTooltip(openTooltip === i ? null : i)}
                />
                <span className="goat-col-reviews">
                  {/* No totalComments fallback: it is a comment count, so under a
                      "Ratings" header it would show the wrong unit entirely. */}
                  {(p.totalReviews ?? 0).toLocaleString()}
                </span>
              </div>
            ))
          )}
        </div>
        </div>

        {selectedCollege && (
          <Link
            to={`/professors?college=${encodeURIComponent(selectedCollege)}&sort=rating`}
            className="goat-view-all"
          >
            View all {selectedCollege} professors →
          </Link>
        )}
      </section>

      {/* ======== Professor Randomizer ======== */}
      <section id="shuffle" className="section randomizer-section" ref={shuffleSectionRef}>
        <div className="randomizer-content">
          <div className="randomizer-text">
            <h2 className="section-title">🎲 Feeling Lucky?</h2>
            <p className="randomizer-desc">
              Discover a random professor and check out their ratings. You might find your next favorite class.
            </p>
          </div>

          <div className={`wheel-spinner ${wheelState} ${slotResult ? 'has-result' : ''}`}>
            <div className="wheel-pointer" />

            <div className="wheel-shell">
              <div
                className="wheel-disc"
                style={wheelState === 'idle'
                  ? undefined
                  : {
                      transform: `rotate(${wheelRotation}deg)`,
                      transition: wheelDurationMs > 0
                        ? `transform ${wheelDurationMs}ms cubic-bezier(0.14, 0.78, 0.18, 1)`
                        : 'none',
                    }
                }
              >
                <div className="wheel-face" />
                {wheelNames.map((name, i) => (
                  <div
                    key={`${i}-${name || 'blank'}`}
                    className="wheel-slice-name"
                    style={{ transform: `rotate(${i * SLICE_DEG}deg) translateY(var(--wheel-label-radius))` }}
                  >
                    <span>{name}</span>
                  </div>
                ))}
              </div>

              <button
                className={`wheel-center-btn ${slotResult ? 'winner' : ''}`}
                onClick={(e) => {
                  e.stopPropagation();
                  handleShuffle();
                }}
                disabled={shuffling}
              >
                {shuffling ? 'Spinning...' : slotResult ? 'Spin Again' : 'Spin'}
              </button>
            </div>

            {slotResult && (
              <div className="wheel-result-card" onClick={() => navigate(`/professors/${slotResult.slug}`, { state: { fromPage: { label: 'Shuffle Wheel', url: '/#shuffle' } } })}>
                <span className="wheel-result-name">{slotResult.name}</span>
                <span className="wheel-result-sub">{slotResult.dept}</span>
              </div>
            )}
          </div>
        </div>
      </section>

      <Footer />
    </div>
  );
};

export default Homepage;