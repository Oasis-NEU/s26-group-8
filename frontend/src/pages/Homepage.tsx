import { useRef } from 'react';
import Navbar from '../components/Navbar';
import SearchBar from '../components/SearchBar';
import Footer from '../components/Footer';
import FeedbackTab from '../components/FeedbackTab';
import ThemeToggle from '../components/ThemeToggle';
import { stats, colleges, trendingProfessors, recentReviews } from '../mock/MockData';
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
  const carouselRef = useRef<HTMLDivElement>(null);

  const scroll = (dir: 'left' | 'right') => {
    if (carouselRef.current) {
      const amount = 300;
      carouselRef.current.scrollBy({
        left: dir === 'left' ? -amount : amount,
        behavior: 'smooth',
      });
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

        {/* College quick-filters */}
        <div className="college-filters">
          {colleges.map((c) => (
            <button key={c} className="college-chip">{c}</button>
          ))}
        </div>
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

      {/* ======== Trending Professors ======== */}
      <section className="section trending-section">
        <div className="section-header">
          <h2 className="section-title">Trending Professors</h2>
          <div className="carousel-controls">
            <button className="carousel-btn" onClick={() => scroll('left')}>
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6" /></svg>
            </button>
            <button className="carousel-btn" onClick={() => scroll('right')}>
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 6 15 12 9 18" /></svg>
            </button>
          </div>
        </div>

        <div className="carousel" ref={carouselRef}>
          {trendingProfessors.map((p) => (
            <div key={p.name} className="prof-card">
              <div className="prof-avatar">{p.name.charAt(0)}</div>
              <h3 className="prof-name">{p.name}</h3>
              <p className="prof-dept">{p.dept}</p>
              <div className="prof-rating">
                <Stars rating={p.rating} />
                <span className="prof-score">{p.rating}</span>
              </div>
              <p className="prof-reviews">{p.reviews} reviews</p>
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