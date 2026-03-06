import { useState, useEffect, useRef } from 'react';
import Navbar from '../components/Navbar';
import SearchBar from '../components/SearchBar';
import Footer from '../components/Footer';
import FeedbackTab from '../components/FeedbackTab';
import ThemeToggle from '../components/ThemeToggle';
import { fetchStats, fetchColleges, fetchGoatProfessors, fetchRandomProfessor } from '../api/api';
import type { Stat, Professor } from '../api/api';
import neuIcon from '../assets/neu-circle-icon.png';
import './Homepage.css';

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
              <div key={s.label} className="stat-item">
                <span className="stat-value">{s.value}</span>
                <span className="stat-label">{s.label}</span>
              </div>
            ))}
      </section>

      {/* ======== GOAT Professors Leaderboard ======== */}
      <section className="section goat-section">
        <div className="section-header">
          <h2 className="section-title">GOATED Professors</h2>
        </div>

        <div className="goat-college-tabs">
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

        <div className="goat-leaderboard">
          <div className="goat-header-row">
            <span className="goat-col-rank">#</span>
            <span className="goat-col-name">Professor</span>
            <span className="goat-col-dept">Department</span>
            <span className="goat-col-rating">Rating</span>
            <span className="goat-col-reviews">Reviews</span>
          </div>

          {profsLoading ? (
            <div className="goat-row" style={{ justifyContent: 'center', opacity: 0.6 }}>
              Loading professors…
            </div>
          ) : profs.length === 0 ? (
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