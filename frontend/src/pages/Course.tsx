import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import StarRating from '../components/StarRating';
import NotFound from './NotFound';
import { fetchCourseData } from '../api/api';
import type { CourseDetail } from '../api/api';
import Footer from '../components/Footer';
import { getInitials, stripPrefix } from '../utils/nameUtils';
import { termSortKey } from '../utils/termUtils';
import SectionHistoryChart from '../components/SectionHistoryChart';
import Breadcrumbs from '../components/Breadcrumbs';
import Seo from '../components/Seo';
import { useAuth } from '../context/AuthContext';
import SignInModal from '../components/SignInModal';
import BookmarkButton from '../components/BookmarkButton';
import './Course.css';

const INITIAL_INSTRUCTORS_VISIBLE = 5;
const INSTRUCTORS_VISIBLE_STEP = 5;

const Course = () => {
	const { code = '' } = useParams<{ code: string }>();
	const { user } = useAuth();
	const [course, setCourse] = useState<CourseDetail | null>(null);
	const [loading, setLoading] = useState(true);
	const [notFound, setNotFound] = useState(false);
	const [visibleInstructorCount, setVisibleInstructorCount] = useState(INITIAL_INSTRUCTORS_VISIBLE);
	const [showBackToTop, setShowBackToTop] = useState(false);
	const [showSignIn, setShowSignIn] = useState(false);
	const tableWrapRef = useRef<HTMLDivElement>(null);
	const [tableAtStart, setTableAtStart] = useState(true);
	const [tableAtEnd, setTableAtEnd] = useState(false);

	useEffect(() => {
		let cancelled = false;

		const load = async () => {
			setLoading(true);
			setNotFound(false);
			const data = await fetchCourseData(code);
			if (cancelled) return;
			if (!data) {
				setCourse(null);
				setNotFound(true);
			} else {
				setCourse(data);
			}
			setLoading(false);
		};

		load();
		return () => {
			cancelled = true;
		};
	}, [code, user]);

	useEffect(() => {
		const handler = () => setShowBackToTop(window.scrollY > 300);
		window.addEventListener('scroll', handler, { passive: true });
		handler();
		return () => window.removeEventListener('scroll', handler);
	}, []);

	useEffect(() => {
		const el = tableWrapRef.current;
		if (!el) return;
		const check = () => {
			setTableAtStart(el.scrollLeft <= 10);
			setTableAtEnd(el.scrollLeft + el.clientWidth >= el.scrollWidth - 10);
		};
		check();
		el.addEventListener('scroll', check, { passive: true });
		window.addEventListener('resize', check);
		return () => {
			el.removeEventListener('scroll', check);
			window.removeEventListener('resize', check);
		};
	}, [course]);

	const recentInstructors = useMemo(() => {
		if (!course) return [];
		const currentYear = new Date().getFullYear();

		const getInstructorsWithinYears = (yearsBack: number) => {
			const cutoffYear = currentYear - yearsBack;
			return course.instructors.filter(inst => {
				const yearMatch = (inst.latestTermTitle || '').match(/\b(20\d{2})\b/);
				return yearMatch && parseInt(yearMatch[1]) >= cutoffYear;
			});
		};

		// Start with 1 year, expand until at least 5 or no more data
		let result = getInstructorsWithinYears(1);
		let yearsBack = 1;
		const maxYear = currentYear - 2000 + 1; // won't go past year 2000
		while (result.length < 5 && yearsBack < maxYear) {
			yearsBack++;
			result = getInstructorsWithinYears(yearsBack);
		}

		// If no recency data available, fall back to all instructors sorted by rating
		if (!result.length) {
			return [...course.instructors]
				.sort((a, b) => (b.avgRating ?? -1) - (a.avgRating ?? -1))
				.slice(0, 10);
		}

		const sorted = result.sort((a, b) => {
			const aTermSort = termSortKey(a.latestTermTitle || '');
			const bTermSort = termSortKey(b.latestTermTitle || '');
			if (bTermSort !== aTermSort) return bTermSort - aTermSort;
			return (b.avgRating ?? -1) - (a.avgRating ?? -1);
		});

		if (sorted.length > 10) {
			return sorted
				.sort((a, b) => (b.avgRating ?? -1) - (a.avgRating ?? -1))
				.slice(0, 10);
		}
		return sorted;
	}, [course]);

	const avgDifficulty = useMemo(() => {
		if (!course) return null;
		const valid = course.instructors.filter(i => i.courseAvgDifficulty != null);
		if (!valid.length) return null;
		return valid.reduce((sum, i) => sum + i.courseAvgDifficulty!, 0) / valid.length;
	}, [course]);

	const avgHoursPerWeek = useMemo(() => {
		if (!course) return null;
		const q = course.questionScores.find(s => s.question.toLowerCase().includes('hours per week'));
		if (q?.avgRating != null) return q.avgRating;
		// Fallback: average instructor-level courseAvgHoursPerWeek
		const valid = course.instructors.filter(i => i.courseAvgHoursPerWeek != null);
		if (!valid.length) return null;
		return valid.reduce((sum, i) => sum + i.courseAvgHoursPerWeek!, 0) / valid.length;
	}, [course]);

	if (loading) {
		return (
			<div className="course-page">
				<div className="course-shell">
					<div className="course-loading">Loading course data...</div>
				</div>
			</div>
		);
	}

	if (notFound || !course) {
		return <NotFound />;
	}

	const summary = course.summary;
	const visibleInstructors = course.instructors.slice(0, visibleInstructorCount);
	const hasMoreInstructors = visibleInstructorCount < course.instructors.length;
	const canCollapseInstructors = visibleInstructorCount > INITIAL_INSTRUCTORS_VISIBLE;
	const hasExpandableInstructors = course.instructors.length > INITIAL_INSTRUCTORS_VISIBLE;

	const courseSeoDescription =
		`${summary.code} (${summary.name}) course reviews and ratings at Northeastern (NEU). ` +
		(summary.avgRating != null ? `Average rating ${summary.avgRating.toFixed(1)}/5. ` : '') +
		(summary.latestTermTitle ? `Last taught ${summary.latestTermTitle}. ` : '') +
		`Compare instructors with TRACE + RateMyProfessor reviews.`;

	const courseCanonical = `https://ratemyhusky.com/courses/${code}`;
	const courseJsonLd: Record<string, unknown> = {
		'@context': 'https://schema.org',
		'@type': 'Course',
		name: `${summary.code} — ${summary.name}`,
		courseCode: summary.code,
		provider: { '@type': 'CollegeOrUniversity', name: 'Northeastern University' },
	};
	if (summary.avgRating != null && summary.ratingCount) {
		courseJsonLd.aggregateRating = {
			'@type': 'AggregateRating',
			ratingValue: summary.avgRating,
			ratingCount: summary.ratingCount,
			bestRating: 5,
		};
	}
	const courseBreadcrumbJsonLd = {
		'@context': 'https://schema.org',
		'@type': 'BreadcrumbList',
		itemListElement: [
			{ '@type': 'ListItem', position: 1, name: 'Home', item: 'https://ratemyhusky.com/' },
			{ '@type': 'ListItem', position: 2, name: 'Courses', item: 'https://ratemyhusky.com/courses' },
			{ '@type': 'ListItem', position: 3, name: summary.code, item: courseCanonical },
		],
	};

	return (
		<div className="course-page">
			<Seo
				title={`${summary.code} Reviews — ${summary.name} at Northeastern`}
				description={courseSeoDescription}
				canonical={courseCanonical}
				jsonLd={[courseJsonLd, courseBreadcrumbJsonLd]}
			/>
			<div className="course-shell">
				<Breadcrumbs items={[
					{ label: 'Courses', to: '/courses' },
					{ label: summary.code },
				]} />

				<header className="course-hero">
					<div>
						<p className="course-code">{summary.code}</p>
						<h1>
							{summary.name}
							<BookmarkButton itemType="course" itemKey={summary.code} size="md" className="course-hero-bookmark" />
						</h1>
						<p className="course-dept">{summary.department}</p>
					</div>
				</header>

				{summary.isTopics && (
					<p className="course-topics-notice">
						This code covers a different class each term, so there is no single
						course rating for it. The per-professor and per-section scores below
						each describe one offering.
					</p>
				)}

				<section className="course-stats-grid">
					<RatingStatCard avgRating={summary.avgRating} />
					<DifficultyStatCard value={avgDifficulty} />
					<StatCard label="Avg Hrs / Week" value={avgHoursPerWeek != null ? `${avgHoursPerWeek.toFixed(1)}h` : '—'} />
					<StatCard label="Instructors" value={course.instructors.length.toLocaleString()} />
					<StatCard label="Avg Enrollment" value={summary.avgEnrollment != null ? summary.avgEnrollment.toLocaleString() : '—'} />
					<StatCard label="Last Taught" value={summary.latestTermTitle || 'Unknown'} className="course-stat-last-taught" />
				</section>

				{recentInstructors.length > 0 && (
					<section className="course-panel">
						<div className="course-panel-header">
							<h2>Recent Professors</h2>
						</div>
						<div className="course-top-prof-grid">
							{recentInstructors.map((prof, index) => (
								<Link
									to={prof.slug ? `/professors/${prof.slug}` : '#'}
									state={prof.slug ? { fromPage: { label: `${code.toUpperCase()} – ${summary.name}`, url: `/courses/${code}` } } : undefined}
									className={`course-top-prof-card${prof.slug ? '' : ' disabled'}`}
									key={`${prof.name}-${index}`}
									aria-label={prof.slug ? `View ${prof.name}` : `${prof.name} profile unavailable`}
									onClick={(e) => {
										if (!prof.slug) e.preventDefault();
									}}
								>
									<div className="course-top-prof-photo">
										{prof.imageUrl ? (
											<img
												className="course-top-prof-img"
												src={prof.imageUrl}
												alt={prof.name}
											/>
										) : (
											<div className="course-top-prof-avatar" aria-hidden="true">
												{getInitials(prof.name)}
											</div>
										)}
										<span className="course-top-prof-rank">#{index + 1}</span>
									</div>
									<div className="course-top-prof-body">
										<div className="course-top-prof-body-top">
											<div className="course-top-prof-rating">
												{prof.avgRating != null ? (
													<>
														<span className="course-top-prof-avg">{prof.avgRating.toFixed(1)}</span>
														<StarRating rating={prof.avgRating} size="sm" />
													</>
												) : (
													<span className="course-top-prof-avg">—</span>
												)}
											</div>
											<h3 className="course-top-prof-name">
												{stripPrefix(prof.name)}
											</h3>
										</div>
										<div className="course-top-prof-footer">
											<span>{prof.totalReviews.toLocaleString()} ratings</span>
											<span className="course-top-prof-footer--center">{prof.totalComments.toLocaleString()} comments</span>
											<span className="course-top-prof-footer--right">{prof.wouldTakeAgainPct != null ? `${Math.round(prof.wouldTakeAgainPct)}% again` : '—'}</span>
										</div>
									</div>
								</Link>
							))}
						</div>
					</section>
				)}

				<section className="course-panel">
					<div className="course-panel-header">
						<h2>Instructor Breakdown</h2>
					</div>
					<div className={`instructor-scroll-wrap${!tableAtStart ? ' fade-left' : ''}${!tableAtEnd ? ' fade-right' : ''}`}>
						<div className="instructor-scroll-inner" ref={tableWrapRef}>
							<div className="instructor-header-row">
								<span>Professor Name</span>
								<span>Rating</span>
								<span>Difficulty</span>
								<span>Hrs / Week</span>
							</div>
							{visibleInstructors.map((row) => (
								<div key={row.name} className="instructor-row">
									<span>{row.name}</span>
											<span>{row.avgRating != null ? row.avgRating.toFixed(2) : '—'}</span>
											<span>{row.courseAvgDifficulty != null ? row.courseAvgDifficulty.toFixed(2) : '—'}</span>
											<span>{row.courseAvgHoursPerWeek != null ? `${row.courseAvgHoursPerWeek.toFixed(1)}h` : '—'}</span>
								</div>
							))}
						</div>
					</div>
					{hasExpandableInstructors && (
						<div className="course-expand-controls">
							<button
								type="button"
								className="course-expand-btn"
								disabled={!canCollapseInstructors}
								onClick={() => setVisibleInstructorCount(INITIAL_INSTRUCTORS_VISIBLE)}
							>
								<span className="course-expand-chevron up" />
							</button>
							<button
								type="button"
								className="course-expand-btn"
								disabled={!hasMoreInstructors}
								onClick={() => setVisibleInstructorCount((prev) => Math.min(prev + INSTRUCTORS_VISIBLE_STEP, course.instructors.length))}
							>
								<span className="course-expand-chevron down" />
							</button>
						</div>
					)}
				</section>

				<section className="course-panel">
					<div className="course-panel-header">
						<h2>Rating History</h2>
					</div>
					{!user ? (
						<div className="course-rating-history-paywall">
							<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="paywall-lock-icon">
								<rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
								<path d="M7 11V7a5 5 0 0 1 10 0v4" />
							</svg>
							<p>Sign in with your <span className="husky-email">husky.neu.edu</span> account to view historical rating trends.</p>
							<button className="paywall-signin-btn" onClick={() => setShowSignIn(true)}>Sign In</button>
						</div>
					) : (
						<SectionHistoryChart sections={course.sections} />
					)}
				</section>

			</div>
			<SignInModal open={showSignIn} onClose={() => setShowSignIn(false)} />
			<button
			className={`back-to-top ${showBackToTop ? 'visible' : ''}`}
			onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
			aria-label="Back to top"
		>
			<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
				<polyline points="18 15 12 9 6 15" />
			</svg>
		</button>
		<Footer />
		</div>
	);
};

function RatingStatCard({ avgRating }: { avgRating: number | null }) {
	return (
		<article className="course-stat-card">
			<strong className="course-stat-value">{avgRating != null ? avgRating.toFixed(2) : '—'}</strong>
			<StarRating rating={avgRating ?? 0} size="lg" />
			<span className="course-stat-label" style={{ display: 'block', textAlign: 'center', position: 'relative' }}>
				Overall Rating
				{avgRating != null && (
					<svg xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ position: 'absolute', top: '50%', transform: 'translateY(-50%)', marginLeft: '4px', opacity: 0.6 }}><circle cx="12" cy="12" r="10" /><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" /><line x1="12" y1="17" x2="12.01" y2="17" /></svg>
				)}
			</span>
			{avgRating != null && (
				<div className="course-stat-breakdown">
					<span>TRACE: {avgRating.toFixed(2)}</span>
				</div>
			)}
		</article>
	);
}

function StatCard({ label, value, className }: { label: string; value: string; className?: string }) {
	return (
		<article className={`course-stat-card${className ? ` ${className}` : ''}`}>
			<strong className="course-stat-value">{value}</strong>
			<span className="course-stat-label">{label}</span>
		</article>
	);
}

function DifficultyStatCard({ value }: { value: number | null }) {
	const color = value == null ? '#eee'
		: value <= 1.5 ? '#27ae60'
		: value <= 2.5 ? '#66bd63'
		: value <= 3.0 ? '#f39c12'
		: value <= 3.5 ? '#e67e22'
		: value <= 4.0 ? '#e74c3c'
		: '#c0392b';
	return (
		<article className="course-stat-card">
			<strong className="course-stat-value">{value != null ? value.toFixed(2) : '—'}</strong>
			<div className="course-difficulty-bar">
				<div className="course-difficulty-fill" style={{ width: `${((value ?? 0) / 5) * 100}%`, background: color }} />
			</div>
			<span className="course-stat-label">Avg Difficulty</span>
		</article>
	);
}

export default Course;
