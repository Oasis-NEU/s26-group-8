import { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import Navbar from '../components/Navbar';
import Footer from '../components/Footer';
import FeedbackTab from '../components/FeedbackTab';
import ThemeToggle from '../components/ThemeToggle';
import Dropdown from '../components/Dropdown';
import StarRating from '../components/StarRating';
import RatingBar from '../components/RatingBar';
import { fetchProfessorData } from '../api/api';
import type { ProfessorProfile, ProfessorReview, TraceComment } from '../api/api';
import neuIcon from '../assets/neu-circle-icon.png';
import './Professor.css';

/* ───────── animated number counter ───────── */
const AnimatedNumber = ({
  value, decimals = 2, suffix = '',
}: { value: number | null; decimals?: number; suffix?: string }) => {
  const [display, setDisplay] = useState('—');
  const hasAnimated = useRef(false);
  const ref = useRef<HTMLSpanElement>(null);
  const animate = useCallback(() => {
    if (hasAnimated.current || value === null) return;
    hasAnimated.current = true;
    const duration = 1200;
    const start = performance.now();
    const step = (now: number) => {
      const t = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - t, 3);
      setDisplay((eased * value).toFixed(decimals) + suffix);
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }, [value, decimals, suffix]);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obs = new IntersectionObserver(([e]) => { if (e.isIntersecting) { animate(); obs.disconnect(); } }, { threshold: 0.5 });
    obs.observe(el);
    return () => obs.disconnect();
  }, [animate]);
  return <span ref={ref}>{display}</span>;
};

/* ───────── grade ordering ───────── */
const GRADE_ORDER = ['A+','A','A-','B+','B','B-','C+','C','C-','D+','D','D-','F','W','WF','P','NP','I'];
const GRADE_COLORS: Record<string, string> = {
  'A+':'#1a9850','A':'#27ae60','A-':'#66bd63',
  'B+':'#a6d96a','B':'#d4e858','B-':'#fee08b',
  'C+':'#fdae61','C':'#f39c12','C-':'#e67e22',
  'D+':'#e74c3c','D':'#d73027','D-':'#c0392b',
  'F':'#a50026','W':'#7f8c8d','WF':'#636e72','P':'#2980b9','NP':'#8e44ad','I':'#999',
};

/* ───────── sort / filter options ───────── */
const sortOptions = [
  { value: 'newest', label: 'Newest First' },
  { value: 'oldest', label: 'Oldest First' },
  { value: 'highest', label: 'Highest Rated' },
  { value: 'lowest', label: 'Lowest Rated' },
];
const courseFilterAll = { value: '__all__', label: 'All Courses' };

/* ───────── tag pill colours ───────── */
const tagColors: Record<string, string> = {
  'Tough Grader':'#e74c3c','Get Ready To Read':'#8e44ad',
  'Participation Matters':'#2980b9','Group Projects':'#16a085',
  'Amazing Lectures':'#27ae60','Clear Grading Criteria':'#2ecc71',
  'Gives Good Feedback':'#1abc9c','Inspirational':'#f39c12',
  'Lots Of Homework':'#e67e22','Hilarious':'#f1c40f',
  'Caring':'#3498db','Respected':'#9b59b6',
  'Lecture Heavy':'#34495e','Test Heavy':'#c0392b',
  'Graded By Few Things':'#d35400','Accessible Outside Class':'#0984e3',
  'Online Savvy':'#6c5ce7',
};
const getTagColor = (tag: string) => tagColors[tag] || '#888';

const ratingColor = (v: number | null) => {
  if (v === null) return '#888';
  if (v >= 4) return '#27ae60';
  if (v >= 3) return '#f39c12';
  return '#e74c3c';
};

/* ═══════════════════════════════════════ */
const Professor = () => {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const reviewsRef = useRef<HTMLElement>(null);
  const [showBackToTop, setShowBackToTop] = useState(false);

  const [profile, setProfile] = useState<ProfessorProfile | null>(null);
  const [reviews, setReviews] = useState<ProfessorReview[]>([]);
  const [traceComments, setTraceComments] = useState<TraceComment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reviewTab, setReviewTab] = useState<'rmp' | 'trace'>('rmp');
  const [sortBy, setSortBy] = useState('newest');
  const [courseFilter, setCourseFilter] = useState('__all__');
  const [visibleReviews, setVisibleReviews] = useState(10);

  /* ── back to top visibility ── */
  useEffect(() => {
    const handler = () => setShowBackToTop(window.scrollY > 300);
    window.addEventListener('scroll', handler, { passive: true });
    return () => window.removeEventListener('scroll', handler);
  }, []);

  /* ── data loading ── */
  useEffect(() => {
    if (!slug) return;
    let cancelled = false;
    async function load() {
      setLoading(true); setError('');
      try {
        const data = await fetchProfessorData(slug);
        if (cancelled) return;
        if (!data) { setError('Professor not found.'); }
        else { setProfile(data); setReviews(data.reviews || []); setTraceComments(data.traceComments || []); }
      } catch { if (!cancelled) setError('Failed to load professor data.'); }
      finally { if (!cancelled) setLoading(false); }
    }
    load();
    return () => { cancelled = true; };
  }, [slug]);

  useEffect(() => { setVisibleReviews(10); }, [sortBy, courseFilter, reviewTab]);

  /* ── derived data ── */
  const uniqueCourses = Array.from(new Set(reviews.map((r) => r.course).filter(Boolean)));
  const courseOptions = [courseFilterAll, ...uniqueCourses.map((c) => ({ value: c, label: c }))];

  const filteredReviews = reviews
    .filter((r) => courseFilter === '__all__' || r.course === courseFilter)
    .sort((a, b) => {
      switch (sortBy) {
        case 'oldest': return new Date(a.date).getTime() - new Date(b.date).getTime();
        case 'highest': return b.quality - a.quality;
        case 'lowest': return a.quality - b.quality;
        default: return new Date(b.date).getTime() - new Date(a.date).getTime();
      }
    });

  const ratingDistribution = [5,4,3,2,1].map((star) => ({
    star, count: reviews.filter((r) => r.quality === star).length,
  }));
  const maxCount = Math.max(...ratingDistribution.map((d) => d.count), 1);

  /* ── grade distribution ── */
  const gradeDistribution = (() => {
    const counts: Record<string, number> = {};
    reviews.forEach((r) => {
      const g = r.grade?.trim();
      if (g && g !== 'N/A' && g !== 'Not sure yet' && g !== 'Rather not say') {
        counts[g] = (counts[g] || 0) + 1;
      }
    });
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    if (total === 0) return [];
    return GRADE_ORDER
      .filter((g) => counts[g])
      .map((g) => ({ grade: g, count: counts[g], pct: (counts[g] / total) * 100, color: GRADE_COLORS[g] || '#999' }));
  })();

  /* ── scroll to reviews ── */
  const scrollToReviews = () => {
    reviewsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  /* ── loading ── */
  if (loading) return (
    <div className="prof-page"><Navbar />
      <div className="prof-loading"><div className="prof-loading-spinner" /><p>Loading professor data…</p></div>
      <ThemeToggle />
    </div>
  );

  /* ── error ── */
  if (error || !profile) return (
    <div className="prof-page"><Navbar />
      <div className="prof-error">
        <span className="prof-error-icon">🔍</span>
        <h2>Professor Not Found</h2>
        <p>{error || "We couldn't find that professor."}</p>
        <button className="prof-back-btn" onClick={() => navigate('/')}>Back to Home</button>
      </div>
      <Footer /><ThemeToggle />
    </div>
  );

  const compareSlug = profile.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

  return (
    <div className="prof-page">
      <Navbar />

      {/* ════════ HERO ════════ */}
      <header className="prof-hero">
        <div className="prof-hero-bg" style={{ backgroundImage: `url(${neuIcon})` }} />
        <div className="prof-hero-glow" />
        <div className="prof-hero-inner">
          <div className="prof-avatar">
            <span>{profile.name.split(' ').map((n) => n[0]).join('')}</span>
          </div>
          <div className="prof-hero-info">
            <h1 className="prof-name">{profile.name}</h1>
            <p className="prof-dept">{profile.department}</p>
            <div className="prof-quick-stats">
              <div className="prof-quick-stat primary">
                <span className="prof-quick-value" style={{ color: ratingColor(profile.avgRating) }}>{profile.avgRating.toFixed(2)}</span>
                <span className="prof-quick-label">Overall</span>
              </div>
              <div className="prof-quick-divider" />
              <div className="prof-quick-stat">
                <span className="prof-quick-value">{profile.rmpRating !== null ? profile.rmpRating.toFixed(2) : '—'}</span>
                <span className="prof-quick-label">RMP</span>
              </div>
              <div className="prof-quick-divider" />
              <div className="prof-quick-stat">
                <span className="prof-quick-value">{profile.traceRating !== null ? profile.traceRating.toFixed(2) : '—'}</span>
                <span className="prof-quick-label">TRACE</span>
              </div>
              <div className="prof-quick-divider" />
              <div className="prof-quick-stat">
                <span className="prof-quick-value">{profile.difficulty !== null ? profile.difficulty.toFixed(1) : '—'}</span>
                <span className="prof-quick-label">Difficulty</span>
              </div>
              {profile.wouldTakeAgainPct !== null && (<>
                <div className="prof-quick-divider" />
                <div className="prof-quick-stat">
                  <span className="prof-quick-value green">{Math.round(profile.wouldTakeAgainPct)}%</span>
                  <span className="prof-quick-label">Would Retake</span>
                </div>
              </>)}
            </div>
            {/* ── action buttons ── */}
            <div className="prof-hero-actions">
              <Link to={`/compare?prof=${compareSlug}`} className="prof-compare-btn">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>
                Compare
              </Link>
              {profile.professorUrl && (
                <a href={profile.professorUrl} target="_blank" rel="noreferrer" className="prof-rmp-btn">
                  View on RMP →
                </a>
              )}
            </div>
          </div>
        </div>
      </header>

      {/* ════════ STAT CARDS ════════ */}
      <section className="prof-stats">
        <div className="prof-stat-card">
          <span className="prof-stat-value accent"><AnimatedNumber value={profile.avgRating} /></span>
          <span className="prof-stat-label">Overall Rating</span>
          <StarRating rating={profile.avgRating ?? 0} size="lg" />
        </div>
        <div className="prof-stat-card">
          <span className="prof-stat-value"><AnimatedNumber value={profile.difficulty} /></span>
          <span className="prof-stat-label">Difficulty</span>
          <div className="prof-difficulty-bar"><div className="prof-difficulty-fill" style={{ width: `${((profile.difficulty ?? 0) / 5) * 100}%` }} /></div>
        </div>
        <div className="prof-stat-card">
          <span className="prof-stat-value green">{profile.wouldTakeAgainPct !== null ? <AnimatedNumber value={profile.wouldTakeAgainPct} decimals={0} suffix="%" /> : '—'}</span>
          <span className="prof-stat-label">Would Take Again</span>
        </div>
        {/* ── clickable → scroll to reviews ── */}
        <div className="prof-stat-card prof-stat-clickable" onClick={scrollToReviews} title="Jump to reviews">
          <span className="prof-stat-value">{profile.totalRatings.toLocaleString()}</span>
          <span className="prof-stat-label">Total Ratings</span>
          <span className="prof-stat-hint">Click to view ↓</span>
        </div>
      </section>

      {/* ════════ CHARTS ROW ════════ */}
      <section className="prof-section prof-charts-row">
        {/* ── Rating Distribution ── */}
        <div className="prof-chart-card">
          <h3 className="prof-chart-title">Rating Distribution</h3>
          <div className="prof-distribution">
            {ratingDistribution.map((d) => (
              <RatingBar key={d.star} star={d.star} count={d.count} max={maxCount} />
            ))}
          </div>
        </div>

        {/* ── Grade Distribution ── */}
        {gradeDistribution.length > 0 && (
          <div className="prof-chart-card">
            <h3 className="prof-chart-title">Grade Distribution</h3>
            <div className="prof-grades">
              {gradeDistribution.map((g) => (
                <div key={g.grade} className="prof-grade-row">
                  <span className="prof-grade-label" style={{ color: g.color }}>{g.grade}</span>
                  <div className="prof-grade-track">
                    <div className="prof-grade-fill" style={{ width: `${g.pct}%`, background: g.color }} />
                  </div>
                  <span className="prof-grade-count">{g.count}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </section>

      {/* ════════ COURSES ════════ */}
      {profile.traceCourses && profile.traceCourses.length > 0 && (() => {
        const grouped = new Map<string, typeof profile.traceCourses>();
        profile.traceCourses.forEach((c) => {
          const code = c.displayName.split(':')[0] || c.displayName;
          if (!grouped.has(code)) grouped.set(code, []);
          grouped.get(code)!.push(c);
        });
        return (
          <section className="prof-section">
            <h2 className="prof-section-title">Courses Taught</h2>
            <div className="prof-courses-compact">
              {Array.from(grouped.entries()).map(([code, sections]) => {
                const nameMatch = sections[0].displayName.match(/\((.+?)\)/);
                const courseName = nameMatch ? nameMatch[1] : '';
                const terms = [...new Set(sections.map((s) => s.termTitle))];
                return (
                  <div key={code} className="prof-course-row">
                    <div className="prof-course-row-main">
                      <span className="prof-course-code">{code}</span>
                      <span className="prof-course-title">{courseName}</span>
                      <span className="prof-course-terms">{sections.length} section{sections.length > 1 ? 's' : ''}</span>
                    </div>
                    <div className="prof-course-term-tags">
                      {terms.map((t) => (<span key={t} className="prof-course-term-tag">{t}</span>))}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        );
      })()}

      {/* ════════ REVIEWS ════════ */}
      <section className="prof-section prof-reviews-section" ref={reviewsRef}>
        <div className="prof-reviews-header">
          <h2 className="prof-section-title">Reviews</h2>
          <div className="prof-review-tabs">
            <button className={`prof-review-tab ${reviewTab === 'rmp' ? 'active' : ''}`} onClick={() => setReviewTab('rmp')}>
              RateMyProfessor ({reviews.length})
            </button>
            <button className={`prof-review-tab ${reviewTab === 'trace' ? 'active' : ''}`} onClick={() => setReviewTab('trace')}>
              TRACE ({traceComments.length})
            </button>
          </div>
        </div>

        {reviewTab === 'rmp' && (<>
          <div className="prof-reviews-filters">
            <Dropdown className="feedback-dropdown" options={sortOptions} value={sortBy} onChange={setSortBy} placeholder="Sort by…" />
            {uniqueCourses.length > 1 && (
              <Dropdown className="feedback-dropdown" options={courseOptions} value={courseFilter} onChange={setCourseFilter} placeholder="Filter by course" />
            )}
          </div>
          <div className="prof-reviews-list">
            {filteredReviews.length === 0 ? <p className="prof-no-reviews">No reviews match the current filters.</p> : (
              filteredReviews.slice(0, visibleReviews).map((r, i) => (
                <div key={i} className="prof-review-card" style={{ animationDelay: `${(i % 10) * 0.04}s`, borderLeftColor: r.quality >= 4 ? '#27ae60' : r.quality >= 3 ? '#f39c12' : '#e74c3c' }}>
                  <div className="prof-review-top">
                    <div className="prof-review-ratings">
                      <div className="prof-review-rating-item">
                        <span className="prof-review-rating-label">Quality</span>
                        <span className="prof-review-rating-value" data-score={r.quality >= 4 ? 'high' : r.quality >= 3 ? 'mid' : 'low'}>{r.quality}</span>
                      </div>
                      <div className="prof-review-rating-item">
                        <span className="prof-review-rating-label">Difficulty</span>
                        <span className="prof-review-rating-value" data-score={r.difficulty <= 2 ? 'high' : r.difficulty <= 3 ? 'mid' : 'low'}>{r.difficulty}</span>
                      </div>
                    </div>
                    <div className="prof-review-meta">
                      {r.course && <span className="prof-review-course">{r.course}</span>}
                      <span className="prof-review-date">{(() => { const d = new Date(r.date); return isNaN(d.getTime()) ? r.date : d.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' }); })()}</span>
                    </div>
                  </div>
                  {r.comment && <p className="prof-review-comment">{r.comment}</p>}
                  <div className="prof-review-bottom">
                    {r.tags && (<div className="prof-review-tags">{r.tags.split(',').map((t: string) => t.trim()).filter(Boolean).map((tag: string, ti: number) => (
                      <span key={ti} className="prof-review-tag" style={{ '--tag-color': getTagColor(tag) } as React.CSSProperties}>{tag}</span>
                    ))}</div>)}
                    <div className="prof-review-pills">
                      {r.grade && r.grade !== 'N/A' && <span className="prof-review-pill">Grade: {r.grade}</span>}
                      {r.attendance && r.attendance !== 'N/A' && <span className="prof-review-pill">Attendance: {r.attendance === 'true' || r.attendance === 'Mandatory' ? 'Mandatory' : 'Not Mandatory'}</span>}
                      {r.textbook && r.textbook !== 'N/A' && <span className="prof-review-pill">Textbook: {r.textbook === 'true' || r.textbook === 'Yes' ? 'Yes' : 'No'}</span>}
                      {r.online_class && r.online_class !== 'N/A' && <span className="prof-review-pill">{r.online_class === 'true' || r.online_class === 'Yes' ? 'Online' : 'In-Person'}</span>}
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
          {visibleReviews < filteredReviews.length && (
            <button className="prof-load-more" onClick={() => setVisibleReviews((v) => v + 10)}>
              Load More Reviews ({filteredReviews.length - visibleReviews} remaining)
            </button>
          )}
        </>)}

        {reviewTab === 'trace' && (
          <div className="prof-reviews-list">
            {traceComments.length === 0 ? <p className="prof-no-reviews">No TRACE comments available.</p> : (
              traceComments.slice(0, visibleReviews).map((c, i) => (
                <div key={i} className="prof-review-card trace-card" style={{ animationDelay: `${(i % 10) * 0.04}s` }}>
                  <span className="prof-trace-question">{c.question}</span>
                  <p className="prof-review-comment">{c.comment}</p>
                </div>
              ))
            )}
            {visibleReviews < traceComments.length && (
              <button className="prof-load-more" onClick={() => setVisibleReviews((v) => v + 10)}>
                Load More ({traceComments.length - visibleReviews} remaining)
              </button>
            )}
          </div>
        )}
      </section>

      <Footer />
      <FeedbackTab />
      <ThemeToggle />

      {/* ════════ BACK TO TOP ════════ */}
      <button
        className={`prof-back-to-top ${showBackToTop ? 'visible' : ''}`}
        onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
        aria-label="Back to top"
      >
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="18 15 12 9 6 15" />
        </svg>
      </button>
    </div>
  );
};

export default Professor;