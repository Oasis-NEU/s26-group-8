import { useState } from 'react';
import Navbar from '../components/Navbar';
import SearchBar from '../components/SearchBar';
import Footer from '../components/Footer';
import FeedbackTab from '../components/FeedbackTab';
import ThemeToggle from '../components/ThemeToggle';
import { stats, colleges, goatProfessors, recentReviews } from '../mock/MockData';
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

      {/* ======== Recent Reviews ======== */}
      <section className="section reviews-section">
        <h2 className="section-title">Recent Reviews</h2>

        <div className="reviews-grid">
          {recentReviews.map((r, i) => (
            <div key={i} className="review-card">
              <div className="review-top">
                <div>
                  <h3 className="review-prof">{r.professor}</h3>
                  <span className="review-course">{r.course}</span>
                </div>
                <Stars rating={r.rating} />
              </div>
              <p className="review-text">{r.text}</p>
              <span className="review-date">{r.date}</span>
            </div>
          ))}
        </div>
      </section>

      <Footer />
      <FeedbackTab />
      <ThemeToggle />
    </div>
  );
};

export default Homepage;