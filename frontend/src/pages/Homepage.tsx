import { useState } from 'react';
import Navbar from '../components/Navbar';
import SearchBar from '../components/SearchBar';
import Footer from '../components/Footer';
import FeedbackTab from '../components/FeedbackTab';
import ThemeToggle from '../components/ThemeToggle';
import { stats, colleges, goatProfessors } from '../mock/MockData';
import neuIcon from '../assets/neu-circle-icon.png';
import './Homepage.css';

/* ---- star renderer ---- */
const Stars = ({ rating }: { rating: number }) => (
  <span className="stars">
    {[1, 2, 3, 4, 5].map((i) => (
      <span key={i} className={i <= Math.round(rating) ? 'star filled' : 'star'}>★</span>
    ))}
  </span>
);

const Homepage = () => {
  const [selectedCollege, setSelectedCollege] = useState(colleges[0]);
  const profs = goatProfessors[selectedCollege] || [];

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
        {stats.map((s) => (
          <div key={s.label} className="stat-item">
            <span className="stat-value">{s.value}</span>
            <span className="stat-label">{s.label}</span>
          </div>
        ))}
      </section>

      {/* ======== GOAT Professors Leaderboard ======== */}
      <section className="section goat-section">
        <div className="section-header">
          <h2 className="section-title">🐐 GOAT Professors</h2>
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

          {profs.map((p, i) => (
            <div
              key={p.name}
              className={`goat-row ${i < 3 ? 'goat-top3' : ''}`}
            >
              <span className="goat-col-rank">
                {i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : i + 1}
              </span>
              <div className="goat-col-name">
                <div className="goat-avatar">{p.name.charAt(0)}</div>
                <span className="goat-name-text">{p.name}</span>
              </div>
              <span className="goat-col-dept">{p.dept}</span>
              <span className="goat-col-rating">
                <Stars rating={p.rating} />
                <span className="goat-score">{p.rating.toFixed(2)}</span>
              </span>
              <span className="goat-col-reviews">{p.reviews}</span>
            </div>
          ))}
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
              onClick={() => {
                const allProfs = Object.values(goatProfessors).flat();
                const random = allProfs[Math.floor(Math.random() * allProfs.length)];
                const slug = random.name.toLowerCase().replace(/[^a-z0-9]+/g, '-');
                window.location.href = `/professors/${slug}`;
              }}
            >
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="16 3 21 3 21 8" />
                <line x1="4" y1="20" x2="21" y2="3" />
                <polyline points="21 16 21 21 16 21" />
                <line x1="15" y1="15" x2="21" y2="21" />
                <line x1="4" y1="4" x2="9" y2="9" />
              </svg>
              Shuffle Professor
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