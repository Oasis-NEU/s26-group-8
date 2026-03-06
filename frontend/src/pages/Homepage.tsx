import { useState, useEffect, useRef, useCallback } from 'react';
import Navbar from '../components/Navbar';
import SearchBar from '../components/SearchBar';
import Footer from '../components/Footer';
import FeedbackTab from '../components/FeedbackTab';
import ThemeToggle from '../components/ThemeToggle';
import { fetchStats, fetchColleges, fetchGoatProfessors, fetchRandomProfessor } from '../api/api';
import type { Stat, Professor } from '../api/api';
import neuIcon from '../assets/neu-circle-icon.png';
import './Homepage.css';

/* ---- animated stat counter ---- */
const AnimatedStat = ({ value, label }: Stat) => {
  const ref = useRef<HTMLDivElement>(null);
  const [display, setDisplay] = useState('0');
  const [hasAnimated, setHasAnimated] = useState(false);

  // Parse "7,600+" → { num: 7600, suffix: "+" }
  const parsed = useRef({ num: 0, suffix: '' });
  useEffect(() => {
    const clean = value.replace(/,/g, '');
    const match = clean.match(/^(\d+)(.*)$/);
    if (match) {
      parsed.current = { num: parseInt(match[1], 10), suffix: match[2] };
    }
  }, [value]);

  const animate = useCallback(() => {
    if (hasAnimated) return;
    setHasAnimated(true);

    const { num, suffix } = parsed.current;
    const duration = 2000;
    const start = performance.now();

    const step = (now: number) => {
      const elapsed = now - start;
      const progress = Math.min(elapsed / duration, 1);
      // Ease out cubic
      const eased = 1 - Math.pow(1 - progress, 3);
      const current = Math.round(eased * num);
      setDisplay(current.toLocaleString() + suffix);

      if (progress < 1) {
        requestAnimationFrame(step);
      }
    };

    requestAnimationFrame(step);
  }, [hasAnimated]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          animate();
          observer.disconnect();
        }
      },
      { threshold: 0.5 }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [animate]);

  return (
    <div className="stat-item" ref={ref}>
      <span className="stat-value">{display}</span>
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
      onClick={onToggle}
    >
      <Stars rating={prof.avgRating} />
      <span className="goat-score">{prof.avgRating.toFixed(2)}</span>
      <span className="goat-rating-hint">ⓘ</span>

      {isOpen && (
        <div className="goat-rating-tooltip">
          <div className="tooltip-row">
            <span className="tooltip-label">RMP</span>
            <span className="tooltip-value">
              {prof.rmpRating !== null ? prof.rmpRating.toFixed(2) : '—'}
            </span>
          </div>
          <div className="tooltip-row">
            <span className="tooltip-label">TRACE</span>
            <span className="tooltip-value">
              {prof.traceRating !== null ? prof.traceRating.toFixed(2) : '—'}
            </span>
          </div>
          <div className="tooltip-divider" />
          <div className="tooltip-row">
            <span className="tooltip-label">Avg Rating</span>
            <span className="tooltip-value tooltip-blended">{prof.avgRating.toFixed(2)}</span>
          </div>
        </div>
      )}
    </span>
  );
};

const Homepage = () => {
  const [stats, setStats] = useState<Stat[]>([]);
  const [colleges, setColleges] = useState<string[]>([]);
  const [selectedCollege, setSelectedCollege] = useState<string>('');
  const [profs, setProfs] = useState<Professor[]>([]);
  const [loading, setLoading] = useState(true);
  const [profsLoading, setProfsLoading] = useState(false);
  const [shuffling, setShuffling] = useState(false);
  const [openTooltip, setOpenTooltip] = useState<number | null>(null);
  const tabsRef = useRef<HTMLDivElement>(null);
  const [tabsAtEnd, setTabsAtEnd] = useState(false);
  const [tabsAtStart, setTabsAtStart] = useState(true);

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
  }, [colleges]);

  // Initial data load
  useEffect(() => {
    async function init() {
      try {
        const [statsData, collegeData] = await Promise.all([
          fetchStats(),
          fetchColleges(),
        ]);
        setStats(statsData);
        setColleges(collegeData);
        if (collegeData.length > 0) {
          setSelectedCollege(collegeData[0]);
        }
      } catch (err) {
        console.error('Failed to load homepage data:', err);
      } finally {
        setLoading(false);
      }
    }
    init();
  }, []);

  // Load GOAT professors when selected college changes
  useEffect(() => {
    if (!selectedCollege) return;
    let cancelled = false;

    async function loadProfs() {
      setProfsLoading(true);
      setOpenTooltip(null);
      try {
        const data = await fetchGoatProfessors(selectedCollege);
        if (!cancelled) setProfs(data);
      } catch (err) {
        console.error('Failed to load professors:', err);
      } finally {
        if (!cancelled) setProfsLoading(false);
      }
    }
    loadProfs();

    return () => { cancelled = true; };
  }, [selectedCollege]);

  const handleShuffle = async () => {
    setShuffling(true);
    try {
      const prof = await fetchRandomProfessor();
      if (prof.url) {
        window.open(prof.url, '_blank');
      } else {
        const slug = prof.name.toLowerCase().replace(/[^a-z0-9]+/g, '-');
        window.location.href = `/professors/${slug}`;
      }
    } catch (err) {
      console.error('Failed to fetch random professor:', err);
    } finally {
      setShuffling(false);
    }
  };

  return (
    <div className="homepage">
      <Navbar />

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
          TRACE evaluations and RateMyProfessor ratings — all in one place.
        </p>

        <SearchBar />
      </main>

      {/* ======== Stats Banner ======== */}
      <section className="stats-banner">
        {loading
          ? Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="stat-item">
                <span className="stat-value">—</span>
                <span className="stat-label">Loading…</span>
              </div>
            ))
          : stats.map((s) => (
              <AnimatedStat key={s.label} value={s.value} label={s.label} />
            ))}
      </section>

      {/* ======== GOAT Professors Leaderboard ======== */}
      <section className="section goat-section">
        <div className="section-header">
          <h2 className="section-title">GOATED Professors</h2>
        </div>

        <div
          className={`goat-college-tabs${tabsAtStart ? ' scrolled-start' : ''}${tabsAtEnd ? ' scrolled-end' : ''}`}
          ref={tabsRef}
        >
          {colleges.map((c) => (
            <button
              key={c}
              className={`goat-tab ${c === selectedCollege ? 'active' : ''}`}
              onClick={() => setSelectedCollege(c)}
            >
              {c}
            </button>
          ))}
        </div>

        <div className={`goat-leaderboard ${profsLoading ? 'goat-loading' : ''}`}>
          <div className="goat-header-row">
            <span className="goat-col-rank">#</span>
            <span className="goat-col-name">Professor</span>
            <span className="goat-col-dept">Department</span>
            <span className="goat-col-rating">Rating</span>
            <span className="goat-col-reviews">Reviews</span>
          </div>

          {profs.length === 0 && !profsLoading ? (
            <div className="goat-row" style={{ justifyContent: 'center', opacity: 0.6 }}>
              No professors found for this college.
            </div>
          ) : (
            profs.map((p, i) => (
              <div
                key={p.name}
                className={`goat-row ${i < 3 ? 'goat-top3' : ''}`}
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
                  {p.totalReviews.toLocaleString()}
                </span>
              </div>
            ))
          )}
        </div>
      </section>

      {/* ======== Professor Randomizer ======== */}
      <section className="section randomizer-section">
        <div className="randomizer-content">
          <div className="randomizer-text">
            <h2 className="section-title">🎲 Feeling Lucky?</h2>
            <p className="randomizer-desc">
              Discover a random professor and check out their ratings. You might find your next favorite class.
            </p>
            <button
              className="randomizer-btn"
              onClick={handleShuffle}
              disabled={shuffling}
            >
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="16 3 21 3 21 8" />
                <line x1="4" y1="20" x2="21" y2="3" />
                <polyline points="21 16 21 21 16 21" />
                <line x1="15" y1="15" x2="21" y2="21" />
                <line x1="4" y1="4" x2="9" y2="9" />
              </svg>
              {shuffling ? 'Shuffling…' : 'Shuffle Professor'}
            </button>
          </div>

          <div className="randomizer-visual">
            <div className="randomizer-dice">🎰</div>
          </div>
        </div>
      </section>

      <Footer />
      <FeedbackTab />
      <ThemeToggle />
    </div>
  );
};

export default Homepage;