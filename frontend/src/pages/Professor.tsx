import { useState, useEffect, useRef, useCallback, useMemo, useLayoutEffect } from 'react';
import { useParams, Link, useLocation } from 'react-router-dom';
import Footer from '../components/Footer';
import NotFound from './NotFound';

import Dropdown from '../components/Dropdown';
import StarRating from '../components/StarRating';
import RatingBar from '../components/RatingBar';
import Breadcrumbs from '../components/Breadcrumbs';
import Seo from '../components/Seo';
import {
  RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  ResponsiveContainer, Legend, Tooltip as RechartsTooltip,
} from 'recharts';
import { fetchProfessorFull } from '../api/api';
import type { ProfessorProfile, ProfessorReview, TraceComment, RedditMention } from '../api/api';
import { termSortKey } from '../utils/termUtils';
import { isPinned, pinnedFirst } from '../utils/askPinMatch';
import { useAuth } from '../context/AuthContext';
import SignInModal from '../components/SignInModal';
import BookmarkButton from '../components/BookmarkButton';
import neuIcon from '../assets/neu-circle-icon.png';
import './Professor.css';

/* ───────── animated number counter ───────── */
const AnimatedNumber = ({
  value, decimals = 2, suffix = '',
}: { value: number | null; decimals?: number; suffix?: string }) => {
  const [display, setDisplay] = useState(value === null ? '—' : '0' + suffix);
  const hasAnimated = useRef(false);
  const prevValue = useRef<number | null>(null);
  const ref = useRef<HTMLSpanElement>(null);

  const animate = useCallback((from: number, to: number) => {
    const duration = 1000;
    const start = performance.now();
    const step = (now: number) => {
      const t = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - t, 3);
      const current = from + (to - from) * eased;
      setDisplay(current.toFixed(decimals) + suffix);
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }, [decimals, suffix]);

  useEffect(() => {
    if (value === null) {
      prevValue.current = null;
      return;
    }
    if (!hasAnimated.current) return;
    if (prevValue.current !== value) {
      animate(prevValue.current || 0, value);
      prevValue.current = value;
    }
  }, [value, animate]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obs = new IntersectionObserver(([e]) => { 
      if (e.isIntersecting && !hasAnimated.current && value !== null) { 
        hasAnimated.current = true;
        animate(0, value);
        prevValue.current = value;
        obs.disconnect(); 
      } 
    }, { threshold: 0.5 });
    obs.observe(el);
    return () => obs.disconnect();
  }, [animate, value]);

  return <span ref={ref}>{display}</span>;
};

/* ───────── term collapse chevron ───────── */
const TermCollapseChevron = () => {
  const ref = useRef<SVGSVGElement>(null);
  const [hasLeftSibling, setHasLeftSibling] = useState(false);

  useLayoutEffect(() => {
    const svg = ref.current;
    if (!svg) return;
    const wrapper = svg.parentElement;
    if (!wrapper) return;
    const check = () => {
      const prev = wrapper.previousElementSibling as HTMLElement | null;
      if (prev) {
        const wTop = wrapper.getBoundingClientRect().top;
        const prevTop = prev.getBoundingClientRect().top;
        setHasLeftSibling(Math.abs(prevTop - wTop) < 5);
      } else {
        setHasLeftSibling(false);
      }
    };
    check();
    const frame = requestAnimationFrame(check);
    const container = wrapper.parentElement;
    const ro = container ? new ResizeObserver(check) : null;
    if (container && ro) ro.observe(container);
    return () => { ro?.disconnect(); cancelAnimationFrame(frame); };
  });

  return hasLeftSibling ? (
    <svg ref={ref} className="prof-term-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6" /></svg>
  ) : (
    <svg ref={ref} className="prof-term-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="18 15 12 9 6 15" /></svg>
  );
};

/* ───────── sort / filter options ───────── */
const sortOptions = [
  { value: 'newest', label: 'Newest First' },
  { value: 'oldest', label: 'Oldest First' },
  { value: 'highest', label: 'Highest Rated' },
  { value: 'lowest', label: 'Lowest Rated' },
];

const traceSortOptions = [
  { value: 'popular', label: 'Most Popular' },
  { value: 'newest', label: 'Most Recent' },
];

const redditSentimentOptions = [
  { value: 'all', label: 'All Sentiment' },
  { value: 'positive', label: 'Positive' },
  { value: 'neutral', label: 'Neutral' },
  { value: 'negative', label: 'Negative' },
];

// Strictly extract "Season Year" from messy term titles
const cleanTerm = (t: string): string => {
  // Match terms like "Fall 2025", "Fall A 2025", "Summer 2 2025"
  const fullMatch = t.match(/(Spring|Fall|Summer|Winter)\s*([A-Z]|\d)?\s*(20\d{2})/i);
  if (fullMatch) {
    const season = fullMatch[1].charAt(0).toUpperCase() + fullMatch[1].slice(1).toLowerCase();
    const modifier = (fullMatch[2] ?? '').trim();
    const year = fullMatch[3];
    return modifier ? `${season} ${modifier} ${year}` : `${season} ${year}`;
  }
  const seasonMatch = t.match(/(Spring|Fall|Summer|Winter)/i);
  if (!seasonMatch) return t.trim();
  const season = seasonMatch[1].charAt(0).toUpperCase() + seasonMatch[1].slice(1).toLowerCase();
  // Fallback: extract year from 6-digit term codes like "202130" → "2021"
  const termCodeMatch = t.match(/(20\d{2})\d{2}/);
  if (termCodeMatch) return `${season} ${termCodeMatch[1]}`;
  return season;
};

const formatReviewDate = (dateStr: string) => {
  if (!dateStr) return '';
  // Convert "2022-12-11 18:36:58 +0000 UTC" to "2022-12-11T18:36:58Z"
  const normalized = dateStr.replace(' +0000 UTC', '').replace(' ', 'T') + 'Z';
  const date = new Date(normalized);
  if (isNaN(date.getTime())) return dateStr;
  return date.toLocaleDateString('en-US', {
    month: 'long',
    day: 'numeric',
    year: 'numeric'
  });
};

const GRADE_ORDER = ['A+','A','A-','B+','B','B-','C+','C','C-','D+','D','D-','F','W','WF','P','NP','I'];
const GRADE_COLORS: Record<string, string> = {
  'A+':'#1a9850','A':'#27ae60','A-':'#66bd63',
  'B+':'#a6d96a','B':'#d4e858','B-':'#fee08b',
  'C+':'#fdae61','C':'#f39c12','C-':'#e67e22',
  'D+':'#e74c3c','D':'#d73027','D-':'#c0392b',
  'F':'#a50026','W':'#7f8c8d','WF':'#636e72','P':'#2980b9','NP':'#8e44ad','I':'#999',
};

/* ───────── near-duplicate detection ───────── */
function normalizeText(s: string): string {
  return s.toLowerCase().replace(/\s+/g, ' ').trim();
}

function deduplicateByText<T>(items: T[], getText: (item: T) => string): T[] {
  const seen = new Set<string>();
  const result: T[] = [];
  for (const item of items) {
    const raw = getText(item);
    if (!raw.trim()) { result.push(item); continue; }
    // Use a truncated normalized form as a fingerprint — catches exact and near-exact dupes
    const norm = normalizeText(raw);
    // Check exact match first
    if (seen.has(norm)) continue;
    // Check prefix-based match (catches 95%+ similar: same text with minor trailing differences)
    const prefix = norm.slice(0, Math.floor(norm.length * 0.9));
    let isDupe = false;
    for (const s of seen) {
      if (s.startsWith(prefix) || norm.startsWith(s.slice(0, Math.floor(s.length * 0.9)))) {
        // Confirm length similarity (within 10%)
        const ratio = Math.min(s.length, norm.length) / Math.max(s.length, norm.length);
        if (ratio >= 0.9) { isDupe = true; break; }
      }
    }
    if (!isDupe) {
      seen.add(norm);
      result.push(item);
    }
  }
  return result;
}

/* ═══════════════════════════════════════ */
const Professor = () => {
  const { slug } = useParams<{ slug: string }>();
  const { user } = useAuth();
  const [showSignIn, setShowSignIn] = useState(false);
  const reviewsRef = useRef<HTMLElement>(null);
  const chartsRef = useRef<HTMLElement>(null);
  const gradesRef = useRef<HTMLDivElement>(null);
  const reviewTabsRef = useRef<HTMLDivElement>(null);
  const questionRefs = useRef<Record<string, HTMLDivElement | null>>({});

  const [profile, setProfile] = useState<ProfessorProfile | null>(null);
  const [reviews, setReviews] = useState<ProfessorReview[]>([]);
  const [traceComments, setTraceComments] = useState<TraceComment[]>([]);
  const [redditMentions, setRedditMentions] = useState<RedditMention[]>([]);
  const [redditSentiment, setRedditSentiment] = useState('all');
  const [redditSearch, setRedditSearch] = useState('');
  const [visibleRedditMentions, setVisibleRedditMentions] = useState(10);
  const [loading, setLoading] = useState(true);
  const [reviewsLoading, setReviewsLoading] = useState(true);
  const [error, setError] = useState('');
  const [reviewTabRestored] = useState(() => sessionStorage.getItem('prof_review_tab') === 'trace');
  const [reviewTab, setReviewTab] = useState<'rmp' | 'trace' | 'reddit'>(() => {
    const saved = sessionStorage.getItem('prof_review_tab');
    if (saved === 'trace') {
      sessionStorage.removeItem('prof_review_tab');
      return 'trace';
    }
    return 'rmp';
  });
  const [sortBy, setSortBy] = useState('newest');
  const [visibleReviews, setVisibleReviews] = useState(10);
  const [selectedCourses, setSelectedCourses] = useState<Set<string>>(new Set());
  const [showBackToTop, setShowBackToTop] = useState(false);
  const [gradesAnimated, setGradesAnimated] = useState(false);
  const [reviewPillStyle, setReviewPillStyle] = useState({ left: 0, width: 0, opacity: 0 });
  const [isReviewPillReady, setIsReviewPillReady] = useState(false);
  const [traceSearch, setTraceSearch] = useState('');
  const [traceSort, setTraceSort] = useState('popular');
  const [expandedQuestions, setExpandedQuestions] = useState<Record<string, boolean>>({});
  const [visibleCommentsPerQuestion, setVisibleCommentsPerQuestion] = useState<Record<string, number>>({});
  const [showAllCourses, setShowAllCourses] = useState(false);
  const [expandedTerms, setExpandedTerms] = useState<Set<string>>(new Set());
  const [closingTerms, setClosingTerms] = useState<Set<string>>(new Set());
  const COURSES_COLLAPSED_LIMIT = 5;
  const MAX_VISIBLE_TERMS = 3;
  const [isImageModalOpen, setIsImageModalOpen] = useState(false);
const [showCourseTip, setShowCourseTip] = useState(() => localStorage.getItem('prof_course_tip_dismissed') !== '1');

  const location = useLocation();
  // Ask pins arrive via navigation state from a clicked citation. Keyed by askedAt so a new
  // question replaces old pins; normal navigation (no askPins) leaves this null.
  const [pinnedSources, setPinnedSources] = useState<{ source: string | null; snippet: string }[]>(
    () => {
      const st = location.state as { askPins?: { sources: { source: string | null; snippet: string }[] } } | null;
      return st?.askPins?.sources ?? [];
    }
  );
  // Sentinel 0 (never a real Date.now() askedAt) so the apply-pins effect's "already applied"
  // guard is false on first mount and actually performs the tab-switch + scroll. The effect
  // sets this ref to the real askedAt once it applies.
  const pinnedAskedAt = useRef<number>(0);
  // Tracks the askedAt we have already scrolled for, so the scroll fires exactly once per new
  // pin set — even though the apply-guard above trips on the effect's second run.
  const scrolledAskedAt = useRef<number>(0);

  /* ── review pill ── */
  const updateReviewPill = useCallback(() => {
    if (!reviewTabsRef.current) return;
    const activeTab = reviewTabsRef.current.querySelector('.prof-review-tab.active') as HTMLElement;
    if (activeTab) {
      setReviewPillStyle({
        left: activeTab.offsetLeft,
        width: activeTab.offsetWidth,
        opacity: 1
      });
    }
  }, []);

  useLayoutEffect(() => {
    if (!loading) {
      updateReviewPill();
    }
  }, [reviewTab, updateReviewPill, loading]);

  useEffect(() => {
    const container = reviewTabsRef.current;
    if (!container || loading) return;

    updateReviewPill();
    const timer = setTimeout(() => setIsReviewPillReady(true), 150);

    const observer = new ResizeObserver(() => {
      setIsReviewPillReady(false);
      updateReviewPill();
      setTimeout(() => setIsReviewPillReady(true), 50);
    });

    observer.observe(container);
    return () => {
      clearTimeout(timer);
      observer.disconnect();
    };
  }, [updateReviewPill, loading]);

  /* ── profile loading ── */
  /* ── combined profile + reviews load (single round-trip) ── */
  useEffect(() => {
    if (!slug) {
      setLoading(false);
      setError('Professor not found.');
      return;
    }
    let cancelled = false;
    async function load() {
      setLoading(true); setReviewsLoading(true); setError('');
      try {
        const data = await fetchProfessorFull(slug!);
        if (cancelled) return;
        if (!data) {
          setError('Professor not found.');
        } else {
          setProfile(data);
          setReviews(data.reviews || []);
          setTraceComments(data.traceComments || []);
          setRedditMentions(data.redditMentions || []);
        }
      } catch { if (!cancelled) setError('Failed to load professor data.'); }
      finally { if (!cancelled) { setLoading(false); setReviewsLoading(false); } }
    }
    load();
    return () => { cancelled = true; };
  }, [slug]);

  /* ── re-fetch profile + reviews on auth change ── */
  const isInitialMount = useRef(true);
  useEffect(() => {
    if (isInitialMount.current) { isInitialMount.current = false; return; }
    if (!slug) return;
    let cancelled = false;
    async function loadOnAuthChange() {
      setReviewsLoading(true);
      try {
        const data = await fetchProfessorFull(slug!);
        if (cancelled) return;
        if (data) {
          setProfile(data);
          setReviews(data.reviews || []);
          setTraceComments(data.traceComments || []);
          setRedditMentions(data.redditMentions || []);
        }
      } catch { /* non-critical */ }
      finally { if (!cancelled) setReviewsLoading(false); }
    }
    loadOnAuthChange();
    return () => { cancelled = true; };
  }, [slug, user]);

  /* ── Ask citation pins: switch to the cited source's tab and scroll to reviews ── */
  useEffect(() => {
    const st = location.state as {
      askPins?: {
        askedAt: number;
        clicked: { source: string | null; snippet: string };
        sources: { source: string | null; snippet: string }[];
      };
    } | null;
    const pins = st?.askPins;
    if (!pins) {
      // Arrived without pins (normal nav / breadcrumb / slug change) — clear any stale pins so
      // a previous question's sources don't ride along and mis-pin rows on this page.
      if (pinnedSources.length > 0) {
        setPinnedSources([]);
        pinnedAskedAt.current = 0;
        scrolledAskedAt.current = 0;
      }
      return;
    }

    // Apply the pins + tab switch once per new askedAt.
    if (pins.askedAt !== pinnedAskedAt.current) {
      pinnedAskedAt.current = pins.askedAt;
      setPinnedSources(pins.sources);
      const src = pins.clicked.source;
      const tab: 'rmp' | 'trace' | 'reddit' =
        src === 'rmp' ? 'rmp' : src === 'trace' ? 'trace' : 'reddit';
      setReviewTab(tab);
    }

    // Scroll once per askedAt, but only after reviews have rendered (reviewsLoading false).
    // On first mount reviewsLoading starts true; this effect re-runs when it flips false
    // (it's in the deps) and the scroll fires then.
    if (!reviewsLoading && scrolledAskedAt.current !== pins.askedAt) {
      scrolledAskedAt.current = pins.askedAt;
      setTimeout(() => reviewsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 150);
    }
  }, [location.state, reviewsLoading]);

  /* ── scroll to reviews after sign-in redirect (mobile: page reload) ── */
  useEffect(() => {
    if (reviewTabRestored && !loading && user && reviewsRef.current) {
      reviewsRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }, [reviewTabRestored, loading, user]);

  /* ── scroll to reviews after sign-in popup (desktop: no reload) ── */
  const prevUserRef = useRef<typeof user>(undefined);
  useEffect(() => {
    const wasLoggedOut = !prevUserRef.current;
    prevUserRef.current = user;
    if (wasLoggedOut && user && sessionStorage.getItem('prof_review_tab') === 'trace') {
      sessionStorage.removeItem('prof_review_tab');
      setReviewTab('trace');
      setTimeout(() => reviewsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 150);
    }
  }, [user]);

  /* ── back to top ── */
  useEffect(() => {
    const handler = () => setShowBackToTop(window.scrollY > 300);
    window.addEventListener('scroll', handler, { passive: true });
    return () => window.removeEventListener('scroll', handler);
  }, []);

  /* ── logic ── */
  const courseCodeMap = useMemo(() => {
    const map = new Map<string, string>();
    profile?.traceCourses?.forEach((c) => {
      const codeMatch = c.displayName.match(/^([A-Z]+)(\d+)/i);
      if (codeMatch) {
        const fullCode = codeMatch[0].toUpperCase();
        map.set(fullCode, fullCode);
        map.set(codeMatch[2], fullCode);
      } else {
        const code = c.displayName.split(':')[0].split(' ')[0].toUpperCase();
        if (code) map.set(code, code);
      }
    });
    return map;
  }, [profile]);

  const getFormattedCourseCode = useCallback((input: string) => {
    if (!input) return '';
    const clean = input.replace(/\s+/g, '').toUpperCase();
    if (courseCodeMap.has(clean)) return courseCodeMap.get(clean)!;
    const match = clean.match(/\d+/);
    if (match && courseCodeMap.has(match[0])) return courseCodeMap.get(match[0])!;
    return clean;
  }, [courseCodeMap]);

  const allCourseCodes = useMemo(() => {
    const codes = new Set<string>();
    profile?.traceCourses?.forEach(c => {
      const m = c.displayName.match(/^([A-Z]+\d+)/);
      const code = (m ? m[1] : c.displayName.split(':')[0].split(' ')[0]).toUpperCase();
      if (code) codes.add(code);
    });
    reviews.forEach(r => {
      const code = getFormattedCourseCode(r.course);
      if (code) codes.add(code.toUpperCase());
    });
    return Array.from(codes).sort();
  }, [profile, reviews, getFormattedCourseCode]);

  const hasInitializedSelection = useRef(false);
  useEffect(() => {
    if (allCourseCodes.length === 0) return;
    if (!hasInitializedSelection.current) {
      setSelectedCourses(new Set(allCourseCodes));
      hasInitializedSelection.current = true;
    } else {
      // Merge any new course codes from reviews into the existing selection
      setSelectedCourses(prev => {
        const next = new Set(prev);
        let changed = false;
        allCourseCodes.forEach(c => { if (!next.has(c)) { next.add(c); changed = true; } });
        return changed ? next : prev;
      });
    }
  }, [allCourseCodes]);

  const filteredRmpReviews = useMemo(() => {
    const filtered = reviews.filter(r => selectedCourses.has(getFormattedCourseCode(r.course).toUpperCase()));
    return deduplicateByText(filtered, r => r.comment);
  }, [reviews, selectedCourses, getFormattedCourseCode]);

  const filteredTraceCourses = useMemo(() => {
    return (profile?.traceCourses || []).filter(c => {
      const m = c.displayName.match(/^([A-Z]+\d+)/);
      const code = (m ? m[1] : c.displayName.split(':')[0].split(' ')[0]).toUpperCase();
      return selectedCourses.has(code);
    });
  }, [profile, selectedCourses]);

  /* ── TRACE radar: precomputed by backend for most-recent term ── */
  const radarData = useMemo(() => {
    if (!profile?.radarData) return null;
    const hasData = profile.radarData.some(p => !p.profMissing);
    return hasData ? profile.radarData : null;
  }, [profile?.radarData]);

  const radarDomainMin = useMemo(() => {
    if (!radarData) return 4;
    const allVals = radarData.flatMap(p => [
      p.profMissing ? null : p.professor,
      p.deptMissing ? null : p.department,
    ]).filter((v): v is number => v !== null && v > 0);
    const min = allVals.length > 0 ? Math.min(...allVals) : 4;
    if (min < 3) return 2;
    if (min < 4) return 3;
    return 4;
  }, [radarData]);

  const stats = useMemo(() => {
    if (!profile) return null;

    const noneSelected = selectedCourses.size === 0;
    const allSelected = allCourseCodes.length > 0 && selectedCourses.size === allCourseCodes.length;

    if (noneSelected) {
      return {
        avgRating: null,
        rmpRating: null,
        traceRating: null,
        difficulty: null,
        totalRatings: null,
        wouldTakeAgainPct: profile.wouldTakeAgainPct,
        hoursPerWeek: null,
      };
    }

    if (allSelected) {
      const traceCompleted = profile.traceRatingCounts
        ? Object.values(profile.traceRatingCounts).reduce((sum, rc) => sum + (rc.completed || 0), 0)
        : 0;
      return {
        avgRating: profile.avgRating,
        rmpRating: profile.rmpRating,
        traceRating: profile.traceRating,
        difficulty: profile.difficulty ?? 0,
        totalRatings: filteredRmpReviews.length + traceCompleted,
        wouldTakeAgainPct: profile.wouldTakeAgainPct,
        hoursPerWeek: profile.hoursPerWeek,
      };
    }

    // Course-filtered: use RMP data for rating/difficulty; fall back to profile-level for TRACE values
    const rmpRating = filteredRmpReviews.length > 0
      ? filteredRmpReviews.reduce((acc, r) => acc + r.quality, 0) / filteredRmpReviews.length
      : null;
    const rmpDifficulty = filteredRmpReviews.length > 0
      ? filteredRmpReviews.reduce((acc, r) => acc + r.difficulty, 0) / filteredRmpReviews.length
      : null;

    const traceRating = profile.traceRating;
    let avgRating = 0;
    if (rmpRating !== null && traceRating !== null) {
      avgRating = (rmpRating + traceRating) / 2;
    } else if (rmpRating !== null) {
      avgRating = rmpRating;
    } else if (traceRating !== null) {
      avgRating = traceRating;
    }

    const coursesWithHours = filteredTraceCourses.filter(c => c.hoursPerWeek != null);
    const filteredHoursPerWeek = coursesWithHours.length > 0
      ? Math.round(coursesWithHours.reduce((acc, c) => acc + c.hoursPerWeek!, 0) / coursesWithHours.length * 10) / 10
      : null;

    let traceWeightedSum = 0, traceResponses = 0;
    for (const c of filteredTraceCourses) {
      if (c.challengeWeightedSum != null && c.challengeResponses != null) {
        traceWeightedSum += c.challengeWeightedSum;
        traceResponses += c.challengeResponses;
      }
    }
    const traceDifficulty = traceResponses > 0 ? traceWeightedSum / traceResponses : null;

    let difficulty: number;
    if (rmpDifficulty !== null && traceDifficulty !== null) {
      difficulty = (rmpDifficulty + traceDifficulty) / 2;
    } else if (rmpDifficulty !== null) {
      difficulty = rmpDifficulty;
    } else if (traceDifficulty !== null) {
      difficulty = traceDifficulty;
    } else {
      difficulty = profile.difficulty ?? 0;
    }

    return {
      avgRating,
      rmpRating,
      traceRating,
      difficulty,
      totalRatings: filteredRmpReviews.length + (profile.traceRatingCounts
        ? Array.from(selectedCourses).reduce((sum, code) => sum + (profile.traceRatingCounts![code]?.completed || 0), 0)
        : 0),
      wouldTakeAgainPct: profile.wouldTakeAgainPct,
      hoursPerWeek: filteredHoursPerWeek,
    };
  }, [profile, filteredRmpReviews, filteredTraceCourses, allCourseCodes, selectedCourses]);

  const ratingDistribution = useMemo(() => {
    const counts = { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 };

    if (selectedCourses.size === 0) {
      return [5, 4, 3, 2, 1].map(star => ({ star, count: 0 }));
    }

    // RMP
    filteredRmpReviews.forEach(r => {
      if (r.quality >= 1 && r.quality <= 5) {
        const q = Math.round(r.quality) as 1 | 2 | 3 | 4 | 5;
        counts[q]++;
      }
    });

    // TRACE — use course-keyed rating counts, filtered by selected courses
    if (profile?.traceRatingCounts) {
      for (const code of selectedCourses) {
        const rc = profile.traceRatingCounts[code];
        if (rc) {
          counts[1] += rc.count1;
          counts[2] += rc.count2;
          counts[3] += rc.count3;
          counts[4] += rc.count4;
          counts[5] += rc.count5;
        }
      }
    }

    return [5, 4, 3, 2, 1].map(star => ({
      star,
      count: counts[star as 1 | 2 | 3 | 4 | 5],
    }));
  }, [filteredRmpReviews, selectedCourses, profile?.traceRatingCounts]);

  const maxCount = useMemo(() => Math.max(...ratingDistribution.map(d => d.count), 1), [ratingDistribution]);

  const gradeDistribution = useMemo(() => {
    const counts: Record<string, number> = {};
    filteredRmpReviews.forEach(r => {
      const g = r.grade?.trim();
      if (g && g !== 'N/A' && g !== 'Not sure yet' && g !== 'Rather not say') {
        counts[g] = (counts[g] || 0) + 1;
      }
    });
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    if (total === 0) return [];
    // Find the range of grades that appear and include all in-between
    const presentIndices = GRADE_ORDER.map((g, i) => counts[g] ? i : -1).filter(i => i >= 0);
    const minIdx = Math.min(...presentIndices);
    const maxIdx = Math.max(...presentIndices);
    return GRADE_ORDER.slice(minIdx, maxIdx + 1).map(g => ({
      grade: g,
      count: counts[g] || 0,
      pct: ((counts[g] || 0) / total) * 100,
      color: GRADE_COLORS[g] || '#999'
    }));
  }, [filteredRmpReviews]);

  useEffect(() => {
    const el = gradesRef.current;
    if (!el) return;
    const obs = new IntersectionObserver(([e]) => {
      if (e.isIntersecting) {
        setGradesAnimated(true);
        obs.disconnect();
      }
    }, { threshold: 0.3 });
    obs.observe(el);
    return () => obs.disconnect();
  }, [profile, gradeDistribution.length]);

  const pinSnippets = useMemo(() => ({
    rmp: pinnedSources.filter((p) => p.source === 'rmp').map((p) => p.snippet),
    trace: pinnedSources.filter((p) => p.source === 'trace').map((p) => p.snippet),
    // sources with null/unknown source came from Reddit historically (see SOURCE_LABEL default)
    reddit: pinnedSources.filter((p) => p.source === 'reddit' || p.source == null).map((p) => p.snippet),
  }), [pinnedSources]);

  const sortedReviews = useMemo(() => {
    const sorted = [...filteredRmpReviews].sort((a, b) => {
      if (sortBy === 'oldest') return new Date(a.date).getTime() - new Date(b.date).getTime();
      if (sortBy === 'highest') return b.quality - a.quality;
      if (sortBy === 'lowest') return a.quality - b.quality;
      return new Date(b.date).getTime() - new Date(a.date).getTime();
    });
    return pinnedFirst(sorted, (r) => r.comment || '', pinSnippets.rmp);
  }, [filteredRmpReviews, sortBy, pinSnippets.rmp]);

  const termIdMap = useMemo(() => {
    const map = new Map<number, string>();
    profile?.traceCourses?.forEach(c => {
      if (!map.has(c.termId)) map.set(c.termId, c.termTitle);
    });
    return map;
  }, [profile]);

  // Map courseId → course code for TRACE comments
  const commentCourseMap = useMemo(() => {
    const map = new Map<number, string>();
    if (!profile?.traceCourses) return map;

    profile.traceCourses.forEach(c => {
      const m = c.displayName.match(/^([A-Z]+\d+)/i);
      const code = m ? m[1].toUpperCase() : '';
      if (code) map.set(c.courseId, code);
    });

    return map;
  }, [profile]);

  const groupedTrace = useMemo(() => {
    const groups: Record<string, TraceComment[]> = {};
    const ids = new Set(filteredTraceCourses.map(c => c.courseId));
    traceComments.forEach(c => {
      if (ids.has(c.courseId)) {
        if (!groups[c.question]) groups[c.question] = [];
        groups[c.question].push(c);
      }
    });
    // Deduplicate near-identical comments within each question group
    for (const q of Object.keys(groups)) {
      groups[q] = deduplicateByText(groups[q], c => c.comment);
    }
    const termKey = (termId: number) => termSortKey(termIdMap.get(termId) || '');
    const searchLower = traceSearch.toLowerCase();
    return Object.entries(groups).map(([q, cs]) => {
      const sortedComments = [...cs].sort((a, b) => {
        if (searchLower) {
          const aMatch = a.comment.toLowerCase().includes(searchLower);
          const bMatch = b.comment.toLowerCase().includes(searchLower);
          if (aMatch && !bMatch) return -1;
          if (!aMatch && bMatch) return 1;
        }
        if (traceSort === 'newest') return termKey(b.termId || 0) - termKey(a.termId || 0);
        return b.comment.length - a.comment.length;
      });
      const withPins = pinnedFirst(sortedComments, (c) => c.comment || '', pinSnippets.trace);
      const hasPin = pinSnippets.trace.length > 0 && withPins.some((c) => isPinned(c.comment || '', pinSnippets.trace));
      return {
        question: q,
        maxTermSortKey: Math.max(...cs.map(c => termKey(c.termId || 0))),
        count: cs.length,
        hasPin,
        comments: withPins,
      };
    }).filter(g =>
      !traceSearch ||
      g.question.toLowerCase().includes(searchLower) ||
      g.comments.some(c => c.comment.toLowerCase().includes(searchLower))
    ).sort((a, b) => {
      if (a.hasPin !== b.hasPin) return a.hasPin ? -1 : 1;
      if (traceSearch) {
        const aM = a.question.toLowerCase().includes(searchLower);
        const bM = b.question.toLowerCase().includes(searchLower);
        if (aM && !bM) return -1;
        if (!aM && bM) return 1;
      }
      if (traceSort === 'newest') return b.maxTermSortKey - a.maxTermSortKey;
      if (traceSort === 'popular') return b.count - a.count;
      return a.question.localeCompare(b.question);
    });
  }, [traceComments, traceSearch, traceSort, filteredTraceCourses, termIdMap, pinSnippets.trace]);

  /* ── Ask citation pins: auto-expand the matching TRACE category (TRACE is gated on `user`) ── */
  useEffect(() => {
    if (pinSnippets.trace.length === 0 || !user) return;
    const hits = groupedTrace.filter((g) => g.hasPin);
    if (hits.length > 0) {
      setExpandedQuestions((p) => {
        const next = { ...p };
        hits.forEach((h) => { next[h.question] = true; });
        return next;
      });
      setVisibleCommentsPerQuestion((p) => {
        const next = { ...p };
        hits.forEach((h) => { next[h.question] = p[h.question] || 5; });
        return next;
      });
    }
  }, [pinSnippets.trace, groupedTrace, user]);

  const redditQuery = redditSearch.trim().toLowerCase();
  const filteredRedditMentions = useMemo(() => {
    const filtered = redditMentions.filter(m =>
      (redditSentiment === 'all' || m.sentiment === redditSentiment) &&
      (redditQuery === '' || m.body.toLowerCase().includes(redditQuery))
    );
    return pinnedFirst(filtered, (m) => m.body || '', pinSnippets.reddit);
  }, [redditMentions, redditSentiment, redditQuery, pinSnippets.reddit]);

  const formatRedditDate = (utc: string | null): string => {
    if (!utc) return '';
    const d = new Date(utc);
    if (isNaN(d.getTime())) return '';
    return d.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });
  };

  const sentimentBorderColor = (s: RedditMention['sentiment']): string =>
    s === 'positive' ? '#27ae60' : s === 'negative' ? '#e74c3c' : s === 'neutral' ? '#f39c12' : '#95a5a6';

  // Reddit permalinks are site-relative (e.g. "/r/NEU/comments/..."); allow those and
  // absolute http(s) URLs only, blocking javascript:/data: and other schemes.
  const safeRedditUrl = (url: string | null): string | null => {
    if (!url) return null;
    if (url.startsWith('/')) return `https://www.reddit.com${url}`;
    try {
      return ['http:', 'https:'].includes(new URL(url).protocol) ? url : null;
    } catch { return null; }
  };

  const toggleCourse = (code: string) => {
    setSelectedCourses(prev => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  };

  const toggleQuestion = (q: string) => {
    const wasExpanded = expandedQuestions[q];
    setExpandedQuestions(p => ({ ...p, [q]: !wasExpanded }));
    // Always reset to 5 when opening (or reopening)
    if (!wasExpanded) {
      setVisibleCommentsPerQuestion(p => ({ ...p, [q]: 5 }));
    }
  };

  const showMoreComments = (e: React.MouseEvent, q: string) => {
    e.stopPropagation();
    setVisibleCommentsPerQuestion(p => ({ ...p, [q]: (p[q] || 5) + 10 }));
  };

  useEffect(() => { setVisibleReviews(10); }, [sortBy, reviewTab]);
  // Reset visible reviews when course filter changes, but only if the filtered list got smaller
  useEffect(() => { setVisibleReviews(v => Math.min(v, Math.max(10, filteredRmpReviews.length))); }, [selectedCourses.size, filteredRmpReviews.length]);

  useEffect(() => {
    if (!isImageModalOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setIsImageModalOpen(false);
    };
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    window.addEventListener('keydown', onKeyDown);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [isImageModalOpen]);

  if (loading) return (
    <div className="prof-page">
      <div className="prof-loading">
        <div className="prof-loading-spinner" />
        <p>Loading professor data…</p>
      </div>
    </div>
  );

  if (error || !profile || !stats) return <NotFound />;

  const seoDescription =
    `${profile.name} professor reviews and ratings: ${profile.avgRating.toFixed(1)}/5 from ${profile.totalRatings} student reviews at Northeastern` +
    (profile.wouldTakeAgainPct != null ? ` (${profile.wouldTakeAgainPct}% would take again)` : '') +
    `. TRACE + RateMyProfessor + Reddit.`;
  const profCanonical = `https://ratemyhusky.com/professors/${slug}`;
  const profJsonLd = {
    '@context': 'https://schema.org',
    '@type': 'ProfilePage',
    dateModified: new Date().toISOString().slice(0, 10),
    mainEntity: {
      '@type': 'Person',
      name: profile.name,
      jobTitle: 'Professor',
      worksFor: {
        '@type': 'CollegeOrUniversity',
        name: 'Northeastern University',
        sameAs: 'https://www.northeastern.edu',
      },
      knowsAbout: profile.department,
      url: profCanonical,
      ...(profile.imageUrl ? { image: profile.imageUrl } : {}),
      ...(profile.professorUrl ? { sameAs: [profile.professorUrl] } : {}),
      // schema.org Person does not support aggregateRating (Google rejects it
      // as an invalid object type in Rich Results), so it is intentionally omitted.
    },
  };
  const profBreadcrumbJsonLd = {
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    itemListElement: [
      { '@type': 'ListItem', position: 1, name: 'Home', item: 'https://ratemyhusky.com/' },
      { '@type': 'ListItem', position: 2, name: 'Professors', item: 'https://ratemyhusky.com/professors' },
      { '@type': 'ListItem', position: 3, name: profile.name, item: profCanonical },
    ],
  };

  return (
    <div className="prof-page">
      <Seo
        title={`${profile.name} Reviews & Ratings — Northeastern ${profile.department}`}
        description={seoDescription}
        canonical={profCanonical}
        image={profile.imageUrl}
        ogType="profile"
        jsonLd={[profJsonLd, profBreadcrumbJsonLd]}
      />
      <header className="prof-hero">
        <div className="prof-hero-bg" style={{ backgroundImage: `url(${neuIcon})` }} />
        <div className="prof-hero-glow" />
        <Breadcrumbs items={[
          { label: 'Professors', to: '/professors' },
          { label: profile.name },
        ]} />
        <div className="prof-hero-inner">
          <div
            className={`prof-avatar ${profile.imageUrl ? 'prof-avatar-clickable' : ''}`}
            onClick={() => {
              if (profile.imageUrl) setIsImageModalOpen(true);
            }}
            role={profile.imageUrl ? 'button' : undefined}
            tabIndex={profile.imageUrl ? 0 : undefined}
            onKeyDown={(e) => {
              if (!profile.imageUrl) return;
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                setIsImageModalOpen(true);
              }
            }}
            aria-label={profile.imageUrl ? `Open larger photo of ${profile.name}` : undefined}
          >
            {profile.imageUrl ? (
              <img
                src={profile.imageUrl}
                alt={profile.name}
                className="prof-avatar-img"
                style={{ objectPosition: `${profile.focusX ?? 50}% ${profile.focusY ?? 30}%` }}
                onError={(e) => {
                  const target = e.currentTarget;
                  target.style.display = 'none';
                  const initials = target.parentElement?.querySelector('.prof-avatar-initials') as HTMLElement;
                  if (initials) initials.style.display = 'flex';
                }}
              />
            ) : null}
            <span
              className="prof-avatar-initials"
              style={profile.imageUrl ? { display: 'none' } : undefined}
            >
              {profile.name.split(' ').map(n => n[0]).join('')}
            </span>
          </div>
          <div className="prof-hero-info">
            <h1 className="prof-name">
              {profile.name}
              <BookmarkButton itemType="professor" itemKey={slug!} size="md" className="prof-hero-bookmark" />
            </h1>
            <p className="prof-dept">{profile.department}</p>
          </div>
        </div>
      </header>

      <section className="prof-stats">
        <div className="prof-stat-card prof-stat-clickable">
          <span className="prof-stat-value">{stats.avgRating !== null ? <AnimatedNumber value={stats.avgRating} /> : '—'}</span>
          <span className="prof-stat-label" style={{ display: 'block', textAlign: 'center', position: 'relative' }}>
            Overall Rating
            {(stats.rmpRating !== null || stats.traceRating !== null) && (
              <svg xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ position: 'absolute', top: '50%', transform: 'translateY(-50%)', marginLeft: '4px', opacity: 0.6 }}><circle cx="12" cy="12" r="10"></circle><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"></path><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>
            )}
          </span>
          <StarRating rating={stats.avgRating ?? 0} size="lg" />
          {(stats.rmpRating !== null || stats.traceRating !== null) && (
            <div className="prof-stat-breakdown">
              {stats.rmpRating !== null && <span>RMP: {stats.rmpRating.toFixed(2)}</span>}
              {stats.traceRating !== null && <span>TRACE: {stats.traceRating.toFixed(2)}</span>}
            </div>
          )}
        </div>
        <div className="prof-stat-card">
          <span className="prof-stat-value">{stats.difficulty != null && stats.difficulty > 0 ? <AnimatedNumber value={stats.difficulty} /> : '—'}</span>
          <span className="prof-stat-label">Difficulty</span>
          <div className="prof-difficulty-bar">
            <div className="prof-difficulty-fill" style={{ 
              width: `${((stats.difficulty ?? 0) / 5) * 100}%`,
              background: (() => {
                const d = stats.difficulty ?? 0;
                if (d <= 1.5) return '#27ae60';
                if (d <= 2.5) return '#66bd63';
                if (d <= 3.0) return '#f39c12';
                if (d <= 3.5) return '#e67e22';
                if (d <= 4.0) return '#e74c3c';
                return '#c0392b';
              })()
            }} />
          </div>
        </div>
        <div className="prof-stat-card">
          <span className={`prof-stat-value ${stats.wouldTakeAgainPct !== null ? 'green' : ''}`}>
            {stats.wouldTakeAgainPct !== null ? <AnimatedNumber value={stats.wouldTakeAgainPct} decimals={0} suffix="%" /> : '—'}
          </span>
          <span className="prof-stat-label">Would Take Again</span>
        </div>
        <div className="prof-stat-card">
          <span className="prof-stat-value">
            {stats.hoursPerWeek !== null && stats.hoursPerWeek !== undefined ? <AnimatedNumber value={stats.hoursPerWeek} decimals={1} suffix="h" /> : '—'}
          </span>
          <span className="prof-stat-label">Hrs / Week</span>
        </div>
        <div className="prof-stat-card prof-stat-clickable" onClick={() => chartsRef.current?.scrollIntoView({ behavior: 'smooth' })}>
          <span className="prof-stat-value">{stats.totalRatings ? stats.totalRatings.toLocaleString() : '—'}</span>
          <span className="prof-stat-label">Total Ratings</span>
          <span className="prof-stat-hint">View distribution ↓</span>
        </div>
        <div className="prof-stat-card prof-stat-clickable" onClick={() => reviewsRef.current?.scrollIntoView({ behavior: 'smooth' })}>
          <span className="prof-stat-value">{(filteredRmpReviews.length + groupedTrace.reduce((acc, g) => acc + g.count, 0)).toLocaleString()}</span>
          <span className="prof-stat-label">Total Comments</span>
          <span className="prof-stat-hint">Read reviews ↓</span>
        </div>
      </section>

      <div className="prof-hero-actions-row">
        <Link 
          to={`/compare?a=${profile.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')}`} 
          className="prof-compare-btn"
        >
          Compare
        </Link>
        {profile.professorUrl && (
          <a href={profile.professorUrl} target="_blank" rel="noreferrer" className="prof-rmp-btn">
            View on RMP →
          </a>
        )}
      </div>

      <section className="prof-section prof-charts-row" ref={chartsRef}>
        <div className="prof-chart-card">
          <h3 className="prof-chart-title">Rating Distribution</h3>
          <div className="prof-distribution">
            {ratingDistribution.map((d) => (
              <RatingBar key={d.star} star={d.star} count={d.count} max={maxCount} />
            ))}
          </div>
        </div>
        {gradeDistribution.length > 0 && (
          <div className="prof-chart-card" ref={gradesRef}>
            <h3 className="prof-chart-title">Grade Distribution</h3>
            <div className="prof-grades">
              {gradeDistribution.map((g) => (
                <div key={g.grade} className="prof-grade-row">
                  <span className="prof-grade-label" style={{ color: g.color }}>{g.grade}</span>
                  <div className="prof-grade-track">
                    <div className="prof-grade-fill" style={{ width: gradesAnimated ? `${g.pct}%` : '0%', background: g.color }} />
                  </div>
                  <span className="prof-grade-count">{g.count}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </section>

      {!user && (profile?.traceCourses?.length ?? 0) > 0 && (
        <section className="prof-radar-section">
          <div className="prof-radar-header">
            <h2 className="prof-section-title">TRACE In-Depth Evaluation</h2>
          </div>
          <div className="prof-trace-paywall">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="paywall-lock-icon">
              <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
              <path d="M7 11V7a5 5 0 0 1 10 0v4" />
            </svg>
            <p>Sign in with your <span className="husky-email">husky.neu.edu</span> account to view the full radar breakdown.</p>
            <button className="paywall-signin-btn" onClick={() => { sessionStorage.setItem('prof_review_tab', 'trace'); setShowSignIn(true); }}>Sign In</button>
          </div>
        </section>
      )}

      {radarData && user && (
        <section className="prof-radar-section">
          <div className="prof-radar-header">
            <h2 className="prof-section-title">TRACE In-Depth Evaluation</h2>
            {profile?.radarTermTitle && (
              <span className="prof-radar-term">{cleanTerm(profile.radarTermTitle)}</span>
            )}
          </div>
          <p className="prof-radar-subtitle">
            How this professor scores across key teaching dimensions compared to their department.
          </p>
          <div className="prof-radar-charts">
            <ResponsiveContainer width="100%" height={340}>
              <RadarChart
                data={radarData}
                margin={{ top: 20, right: 40, bottom: 20, left: 40 }}
              >
                <PolarGrid stroke="var(--radar-grid, #e0e0e0)" />
                <PolarAngleAxis
                  dataKey="metric"
                  tick={(props: any) => {
                    const { x, y, cy, payload, textAnchor } = props;
                    const point = radarData?.find(p => p.metric === payload.value);
                    const rating = point && !point.profMissing ? point.professor.toFixed(2) : null;
                    // Shift the whole label up when it's above center; extra offset for the topmost label
                    const isTop = y < cy;
                    const adjustedY = rating && isTop ? y - (payload.value === 'Teaching' ? 22 : 10) : y;
                    return (
                      <text x={x} y={adjustedY} textAnchor={textAnchor} fill="var(--radar-label, #555)" fontFamily="Nunito, sans-serif">
                        <tspan x={x} fontSize={12} fontWeight={600}>{payload.value}</tspan>
                        {rating && (
                          <tspan x={x} dy={15} fontSize={11} fontWeight={400} fill="var(--radar-label, #777)">{rating} / 5.0</tspan>
                        )}
                      </text>
                    );
                  }}
                />
                <PolarRadiusAxis
                  domain={[radarDomainMin, 5]}
                  tick={(props: any) => {
                    const { x, y, payload } = props;
                    return (
                      <text x={x} y={y} textAnchor="middle" dominantBaseline="central" fontSize={11} fontWeight={600} fontFamily="Nunito, sans-serif" fill="#444" stroke="#fff" strokeWidth={2} paintOrder="stroke">{payload.value}</text>
                    );
                  }}
                  axisLine={false}
                  ticks={radarDomainMin === 2 ? [2, 3, 4, 5] : radarDomainMin === 3 ? [3, 4, 5] : [4, 5]}
                  angle={90}
                />
                <RechartsTooltip
                  contentStyle={{
                    background: 'var(--radar-tooltip-bg, #fff)',
                    border: '1px solid var(--radar-tooltip-border, #e0e0e0)',
                    borderRadius: 8,
                    fontSize: 13,
                    color: 'var(--radar-tooltip-text, #333)',
                    fontFamily: 'Nunito, sans-serif',
                  }}
                  formatter={(value, name) =>
                    typeof value === 'number' && value > 0 ? [`${value.toFixed(2)} / 5`, String(name)] : ['N/A', String(name)]
                  }
                />
                <Legend
                  wrapperStyle={{ fontFamily: 'Nunito, sans-serif', fontSize: 13, fontWeight: 700, paddingTop: 8 }}
                />
                {/* Professor — red, rendered first (behind) */}
                <Radar
                  name="This Professor"
                  dataKey="professor"
                  stroke="#d6394c"
                  fill="#d6394c"
                  fillOpacity={0.35}
                  strokeWidth={2}
                  dot={{ r: 3, fill: '#d6394c', strokeWidth: 0 }}
                />
                {/* Department average — gray, rendered on top */}
                {radarData && radarData.some(p => !p.deptMissing) && (
                  <Radar
                    name="Dept. Average"
                    dataKey="department"
                    stroke="#888"
                    fill="#888"
                    fillOpacity={0.45}
                    strokeWidth={2}
                    dot={{ r: 3, fill: '#888', strokeWidth: 0 }}
                  />
                )}
              </RadarChart>
            </ResponsiveContainer>
          </div>
          <div className="prof-radar-legend-labels">
            {([
              { short: 'Teaching', desc: 'Overall teaching effectiveness & clear communication' },
              { short: 'Organization', desc: 'Course materials, syllabus accuracy & time usage' },
              { short: 'Rigor', desc: 'Intellectual challenge & how much students learned' },
              { short: 'Grading', desc: 'Fair evaluation & quality of feedback' },
              { short: 'Accessibility', desc: 'Office hours availability & inclusive environment' },
            ] as const).map(m => (
              <div key={m.short} className="prof-radar-metric-info">
                <span className="prof-radar-metric-name">{m.short}</span>
                <span className="prof-radar-metric-desc">{m.desc}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {isImageModalOpen && profile.imageUrl && (
        <div className="prof-image-modal-overlay" onClick={() => setIsImageModalOpen(false)}>
          <div className="prof-image-modal" onClick={(e) => e.stopPropagation()}>
            <button
              className="prof-image-modal-close"
              onClick={() => setIsImageModalOpen(false)}
              aria-label="Close enlarged professor image"
            >
              ×
            </button>
            <img src={profile.imageUrl} alt={profile.name} className="prof-image-modal-img" />
          </div>
        </div>
      )}

      {allCourseCodes.length > 0 && (() => {
        const grouped = new Map<string, typeof profile.traceCourses>();
        profile.traceCourses?.forEach(c => {
          const match = c.displayName.match(/^([A-Z]+\d+)/);
          const code = (match ? match[1] : c.displayName.split(':')[0].split(' ')[0]).toUpperCase();
          if (!grouped.has(code)) grouped.set(code, []);
          grouped.get(code)!.push(c);
        });
        reviews.forEach(r => {
          const code = getFormattedCourseCode(r.course).toUpperCase();
          if (code && !grouped.has(code)) grouped.set(code, []);
        });
        // Build a map of course code -> most recent review date
        const recentReviewDate = new Map<string, number>();
        reviews.forEach(r => {
          const code = getFormattedCourseCode(r.course).toUpperCase();
          if (code && r.date) {
            const ts = new Date(r.date).getTime();
            if (!recentReviewDate.has(code) || ts > recentReviewDate.get(code)!) {
              recentReviewDate.set(code, ts);
            }
          }
        });
        const sorted = Array.from(grouped.entries()).sort(([a, secA], [b, secB]) => {
          // Primary: most recent review date (descending)
          const reviewA = recentReviewDate.get(a) || 0;
          const reviewB = recentReviewDate.get(b) || 0;
          if (reviewA !== reviewB) return reviewB - reviewA;
          // Secondary: most recent term from trace data (descending by proper chronological order)
          const termA = secA.length > 0 ? Math.max(...secA.map(s => termSortKey(s.termTitle))) : 0;
          const termB = secB.length > 0 ? Math.max(...secB.map(s => termSortKey(s.termTitle))) : 0;
          if (termA !== termB) return termB - termA;
          // Tertiary: alphabetical
          return a.localeCompare(b);
        });
        return (
          <section className="prof-section">
            <div className="prof-section-header">
              <h2 className="prof-section-title">Courses Taught</h2>
              <div className="prof-section-actions">
                <button className="prof-action-link" onClick={() => setSelectedCourses(new Set(allCourseCodes))}>Select All</button>
                <button className="prof-action-link" onClick={() => setSelectedCourses(new Set())}>Clear All</button>
              </div>
            </div>
            <div className="prof-courses-compact">
              {sorted.slice(0, COURSES_COLLAPSED_LIMIT).map(([code, sections]) => {
                const nameMatch = sections[0]?.displayName.match(/\((.+?)\)/);
                const courseName = nameMatch ? nameMatch[1] : '';
                const terms = [...new Set(sections.map(s => cleanTerm(s.termTitle)))].filter(t => /\b20\d{2}\b/.test(t)).sort((a, b) => termSortKey(b) - termSortKey(a));
                const termsExpanded = expandedTerms.has(code);
                const hiddenTermCount = terms.length - MAX_VISIBLE_TERMS;
                const isSelected = selectedCourses.has(code);
                return (
                  <div
                    key={code}
                    className={`prof-course-row ${isSelected ? 'selected' : ''}`}
                    onClick={() => toggleCourse(code)}
                  >
                    <div className="prof-course-row-main">
                      <span className="prof-course-code">{code}</span>
                      <span className="prof-course-title">{courseName || 'Course data from reviews'}</span>
                      <span className="prof-course-terms">
                        {sections.length > 0 ? `${sections.length} section${sections.length > 1 ? 's' : ''}` : 'RMP reviews only'}
                      </span>
                    </div>
                    <div className="prof-course-lower">
                      {terms.length > 0 && (
                        <div className="prof-course-term-tags">
                          {terms.slice(0, MAX_VISIBLE_TERMS).map(t => <span key={t} className="prof-course-term-tag">{t}</span>)}
                          {hiddenTermCount > 0 && !termsExpanded && !closingTerms.has(code) && (
                            <span
                              className="prof-course-term-tag prof-course-term-more"
                              onClick={(e) => { e.stopPropagation(); setExpandedTerms(prev => { const next = new Set(prev); next.add(code); return next; }); }}
                            >
                              +{hiddenTermCount} more
                            </span>
                          )}
                          {terms.slice(MAX_VISIBLE_TERMS).map((t, i) => {
                            const isClosing = closingTerms.has(code);
                            const reverseI = hiddenTermCount - 1 - i;
                            return <span key={t} className={`prof-course-term-tag prof-course-term-hidden ${termsExpanded || isClosing ? 'visible' : ''} ${isClosing ? 'closing' : ''}`} style={isClosing ? { animationDelay: `${reverseI * 0.04}s` } : termsExpanded ? { animationDelay: `${i * 0.04}s` } : undefined}>{t}</span>;
                          })}
                          {hiddenTermCount > 0 && (termsExpanded || closingTerms.has(code)) && (
                            <span
                              className={`prof-course-term-tag prof-course-term-more ${closingTerms.has(code) ? 'closing' : ''}`}
                              onClick={(e) => {
                                e.stopPropagation();
                                if (closingTerms.has(code)) return;
                                setClosingTerms(prev => { const next = new Set(prev); next.add(code); return next; });
                                setTimeout(() => {
                                  setExpandedTerms(prev => { const next = new Set(prev); next.delete(code); return next; });
                                  setClosingTerms(prev => { const next = new Set(prev); next.delete(code); return next; });
                                }, hiddenTermCount * 40 + 200);
                              }}
                            >
                              <TermCollapseChevron />
                            </span>
                          )}
                        </div>
                      )}
                      {sections.length > 0 && (
                        <div className="prof-course-view">
                          <Link
                            to={`/courses/${code.toLowerCase()}`}
                            state={{ fromPage: { label: profile.name, url: `/professors/${slug}` } }}
                            className="prof-course-view-btn"
                            onClick={(e) => e.stopPropagation()}
                          >
                            View Course
                          </Link>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
              {sorted.length > COURSES_COLLAPSED_LIMIT && (
                <>
                  <div className={`prof-courses-extra ${showAllCourses ? 'open' : ''}`}>
                    <div>{sorted.slice(COURSES_COLLAPSED_LIMIT).map(([code, sections]) => {
                      const nameMatch = sections[0]?.displayName.match(/\((.+?)\)/);
                      const courseName = nameMatch ? nameMatch[1] : '';
                      const terms = [...new Set(sections.map(s => cleanTerm(s.termTitle)))].filter(t => /\b20\d{2}\b/.test(t)).sort((a, b) => termSortKey(b) - termSortKey(a));
                      const termsExpanded = expandedTerms.has(code);
                            const hiddenTermCount = terms.length - MAX_VISIBLE_TERMS;
                      const isSelected = selectedCourses.has(code);
                      return (
                        <div
                          key={code}
                          className={`prof-course-row ${isSelected ? 'selected' : ''}`}
                          onClick={() => toggleCourse(code)}
                        >
                          <div className="prof-course-row-main">
                            <span className="prof-course-code">{code}</span>
                            <span className="prof-course-title">{courseName || 'Course data from reviews'}</span>
                            <span className="prof-course-terms">
                              {sections.length > 0 ? `${sections.length} section${sections.length > 1 ? 's' : ''}` : 'RMP reviews only'}
                            </span>
                          </div>
                          <div className="prof-course-lower">
                            {terms.length > 0 && (
                              <div className="prof-course-term-tags">
                                {terms.slice(0, MAX_VISIBLE_TERMS).map(t => <span key={t} className="prof-course-term-tag">{t}</span>)}
                                {hiddenTermCount > 0 && !termsExpanded && !closingTerms.has(code) && (
                                  <span
                                    className="prof-course-term-tag prof-course-term-more"
                                    onClick={(e) => { e.stopPropagation(); setExpandedTerms(prev => { const next = new Set(prev); next.add(code); return next; }); }}
                                  >
                                    +{hiddenTermCount} more
                                  </span>
                                )}
                                {terms.slice(MAX_VISIBLE_TERMS).map((t, i) => {
                                  const isClosing = closingTerms.has(code);
                                  const reverseI = hiddenTermCount - 1 - i;
                                  return <span key={t} className={`prof-course-term-tag prof-course-term-hidden ${termsExpanded || isClosing ? 'visible' : ''} ${isClosing ? 'closing' : ''}`} style={isClosing ? { animationDelay: `${reverseI * 0.04}s` } : termsExpanded ? { animationDelay: `${i * 0.04}s` } : undefined}>{t}</span>;
                                })}
                                {hiddenTermCount > 0 && (termsExpanded || closingTerms.has(code)) && (
                                  <span
                                    className={`prof-course-term-tag prof-course-term-more ${closingTerms.has(code) ? 'closing' : ''}`}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      if (closingTerms.has(code)) return;
                                      setClosingTerms(prev => { const next = new Set(prev); next.add(code); return next; });
                                      setTimeout(() => {
                                        setExpandedTerms(prev => { const next = new Set(prev); next.delete(code); return next; });
                                        setClosingTerms(prev => { const next = new Set(prev); next.delete(code); return next; });
                                      }, hiddenTermCount * 40 + 200);
                                    }}
                                  >
                                    <TermCollapseChevron />
                                  </span>
                                )}
                              </div>
                            )}
                            {sections.length > 0 && (
                              <div className="prof-course-view">
                                <Link
                                  to={`/courses/${code.toLowerCase()}`}
                                  state={{ fromPage: { label: profile.name, url: `/professors/${slug}` } }}
                                  className="prof-course-view-btn"
                                  onClick={(e) => e.stopPropagation()}
                                >
                                  View Course
                                </Link>
                              </div>
                            )}
                          </div>
                        </div>
                      );
                    })}</div>
                  </div>
                  <button
                    className="prof-courses-toggle"
                    onClick={() => setShowAllCourses(v => !v)}
                  >
                    {showAllCourses ? 'Show fewer' : `+${sorted.length - COURSES_COLLAPSED_LIMIT} more courses`}
                    <svg
                      className={`prof-courses-toggle-icon ${showAllCourses ? 'expanded' : ''}`}
                      width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
                    >
                      <polyline points="6 9 12 15 18 9" />
                    </svg>
                  </button>
                </>
              )}
            </div>
          </section>
        );
      })()}

      {showCourseTip && allCourseCodes.length > 0 && (
        <div className="prof-course-tip-wrapper">
          <div className="prof-course-tip">
            <div className="prof-course-tip-icon">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="8" x2="12" y2="12" />
                <line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
            </div>
            <div className="prof-course-tip-body">
              <div className="prof-course-tip-label">Tip</div>
              <p className="prof-course-tip-text">
                To filter reviews by course, click <strong>Clear All</strong> in the Courses Taught section, then select the course you want to see reviews for.
              </p>
            </div>
            <button className="prof-course-tip-close" onClick={() => { localStorage.setItem('prof_course_tip_dismissed', '1'); setShowCourseTip(false); }} aria-label="Dismiss tip">
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
        </div>
      )}

      <section className="prof-section prof-reviews-section" ref={reviewsRef}>
        <div className="prof-reviews-header">
          <h2 className="prof-section-title">Reviews</h2>
          <div className="prof-review-tabs" ref={reviewTabsRef}>
            <div 
              className={`prof-review-pill-background ${isReviewPillReady ? 'animate' : ''}`} 
              style={{ 
                transform: `translateX(${reviewPillStyle.left}px)`, 
                width: `${reviewPillStyle.width}px`, 
                opacity: reviewPillStyle.opacity, 
                visibility: reviewPillStyle.opacity === 0 ? 'hidden' : 'visible' 
              }} 
            />
            <button className={`prof-review-tab ${reviewTab === 'rmp' ? 'active' : ''}`} onClick={() => setReviewTab('rmp')}>
              <span className="prof-review-tab-full">RateMyProfessor ({filteredRmpReviews.length})</span>
              <span className="prof-review-tab-short">RMP ({filteredRmpReviews.length})</span>
            </button>
            <button className={`prof-review-tab ${reviewTab === 'trace' ? 'active' : ''}`} onClick={() => setReviewTab('trace')}>
              <span className="prof-review-tab-full">TRACE ({groupedTrace.reduce((acc, g) => acc + g.count, 0)})</span>
              <span className="prof-review-tab-short">TRACE ({groupedTrace.reduce((acc, g) => acc + g.count, 0)})</span>
            </button>
            <button className={`prof-review-tab ${reviewTab === 'reddit' ? 'active' : ''}`} onClick={() => setReviewTab('reddit')}>
              <span className="prof-review-tab-full">Reddit ({redditMentions.length})</span>
              <span className="prof-review-tab-short">Reddit ({redditMentions.length})</span>
            </button>
          </div>
        </div>

        {reviewsLoading && (
          <div className="prof-loading" style={{ padding: '2rem 0' }}>
            <div className="prof-loading-spinner" />
          </div>
        )}

        {!reviewsLoading && reviewTab === 'rmp' && (
          <>
            <div className="prof-reviews-filters">
              <Dropdown className="feedback-dropdown" options={sortOptions} value={sortBy} onChange={setSortBy} placeholder="Sort by…" />
            </div>
            <div className="prof-reviews-list">
              {sortedReviews.length === 0 ? (
                <p className="prof-no-reviews">No reviews match current filters.</p>
              ) : (
                sortedReviews.slice(0, visibleReviews).map((r, i) => (
                  <div key={i} className={`prof-review-card ${isPinned(r.comment || '', pinSnippets.rmp) ? 'is-ask-pinned' : ''}`} style={{ borderLeftColor: r.quality >= 4 ? '#27ae60' : r.quality >= 3 ? '#f39c12' : '#e74c3c' }}>
                    {isPinned(r.comment || '', pinSnippets.rmp) && <span className="ask-pinned-label">From your question</span>}
                    <div className="prof-review-top">
                      <div className="prof-review-ratings">
                        <div className="prof-review-rating-item">
                          <span className="prof-review-rating-label">Quality</span>
                          <span className="prof-review-rating-value" data-score={String(r.quality)}>{r.quality}</span>
                        </div>
                        <div className="prof-review-rating-item">
                          <span className="prof-review-rating-label">Difficulty</span>
                          <span className="prof-review-rating-value" data-score={String(6 - r.difficulty)}>{r.difficulty}</span>
                        </div>
                      </div>
                      <div className="prof-review-meta">
                        <span className="prof-review-course">{getFormattedCourseCode(r.course)}</span>
                        <span className="prof-review-date">{formatReviewDate(r.date)}</span>
                      </div>
                    </div>
                    {r.comment && <p className="prof-review-comment">{r.comment}</p>}
                    <div className="prof-review-bottom">
                      {r.tags && (
                        <div className="prof-review-tags">
                          {r.tags.split('--').map(t => t.trim()).filter(Boolean).map((t, ti) => (
                            <span key={ti} className="prof-review-tag">{t}</span>
                          ))}
                        </div>
                      )}
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
            {visibleReviews < sortedReviews.length && (
              <button className="prof-load-more" onClick={() => setVisibleReviews(v => v + 10)}>
                Load More
              </button>
            )}
          </>
        )}

        {!reviewsLoading && reviewTab === 'reddit' && (
          <>
            <div className="prof-trace-controls">
              <div className="trace-search-container">
                <input
                  type="text"
                  className="trace-search-input"
                  placeholder="Search Reddit mentions..."
                  value={redditSearch}
                  onChange={e => { setRedditSearch(e.target.value); setVisibleRedditMentions(10); }}
                />
              </div>
              <Dropdown className="trace-sort-dropdown" options={redditSentimentOptions} value={redditSentiment} onChange={(v) => { setRedditSentiment(v); setVisibleRedditMentions(10); }} />
            </div>
            <div className="prof-trace-categories">
              {filteredRedditMentions.length === 0 ? (
                <p className="prof-no-reviews">No Reddit mentions found for this professor.</p>
              ) : (
                filteredRedditMentions.slice(0, visibleRedditMentions).map((m, i) => {
                  const redditUrl = safeRedditUrl(m.permalink);
                  const dateStr = formatRedditDate(m.created_utc);
                  const sScore = m.sentiment_score ?? 0;
                  // Clamp the pointer so the label/arrow never clip the card edge at extreme scores.
                  const markerPct = Math.min(90, Math.max(10, (sScore + 1) / 2 * 100));
                  const magnitudePct = Math.round(Math.abs(sScore) * 100);
                  const word = m.sentiment ? m.sentiment.charAt(0).toUpperCase() + m.sentiment.slice(1) : 'Neutral';
                  const label = `${word} (${magnitudePct}%)`;
                  return (
                  <div key={i} className={`reddit-comment-bubble ${isPinned(m.body || '', pinSnippets.reddit) ? 'is-ask-pinned' : ''}`}>
                    {isPinned(m.body || '', pinSnippets.reddit) && <span className="ask-pinned-label">From your question</span>}
                    <div className="reddit-comment-meta">
                      {dateStr && <span className="reddit-comment-date">{dateStr}</span>}
                      <div className="reddit-comment-actions">
                        {m.score != null && <span className="reddit-comment-score">▲ {m.score}</span>}
                        {redditUrl && <a className="reddit-comment-link" href={redditUrl} target="_blank" rel="noopener noreferrer">View on Reddit ↗</a>}
                      </div>
                    </div>
                    <p className="reddit-comment-body">{m.body}</p>
                    <div className="reddit-sentiment">
                      <div className="reddit-sentiment-pointer" style={{ left: `${markerPct}%` }}>
                        <span className="reddit-sentiment-label" style={{ color: sentimentBorderColor(m.sentiment) }}>{label}</span>
                        <span className="reddit-sentiment-arrow" style={{ color: sentimentBorderColor(m.sentiment) }}>▼</span>
                      </div>
                      <div className="reddit-sentiment-track" />
                    </div>
                  </div>
                  );
                })
              )}
            </div>
            {visibleRedditMentions < filteredRedditMentions.length && (
              <button className="prof-load-more" onClick={() => setVisibleRedditMentions(v => v + 10)}>
                Load More
              </button>
            )}
          </>
        )}

        {!reviewsLoading && reviewTab === 'trace' && (
          <div className="prof-trace-container">
            {user && (
              <div className="prof-trace-controls">
                <div className="trace-search-container">
                  <input
                    type="text"
                    className="trace-search-input"
                    placeholder="Search comments or questions..."
                    value={traceSearch}
                    onChange={e => setTraceSearch(e.target.value)}
                  />
                </div>
                <Dropdown className="trace-sort-dropdown" options={traceSortOptions} value={traceSort} onChange={setTraceSort} />
              </div>
            )}
            <div className="prof-trace-categories">
              {groupedTrace.map(g => {
                const isExpanded = expandedQuestions[g.question];
                const visibleCount = visibleCommentsPerQuestion[g.question] || 5;
                return (
                  <div key={g.question} className={`trace-category-item ${isExpanded ? 'expanded' : ''}`} ref={el => { questionRefs.current[g.question] = el; }}>
                    <div className="trace-category-header" onClick={() => toggleQuestion(g.question)}>
                      <div className="trace-category-title-wrap">
                        <h4 className="trace-category-title">{g.question}</h4>
                        <div className="trace-category-subtitle">
                          <span className="trace-comment-count">{g.count} comments</span>
                        </div>
                      </div>
                      <svg className="trace-chevron" xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>
                    </div>
                    {isExpanded && !user && (
                      <div className="trace-category-paywall">
                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="paywall-lock-icon-sm">
                          <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
                          <path d="M7 11V7a5 5 0 0 1 10 0v4" />
                        </svg>
                        <p>Sign in with your <span className="husky-email">husky.neu.edu</span> account to read these comments.</p>
                        <button className="paywall-signin-btn small" onClick={(e) => { e.stopPropagation(); sessionStorage.setItem('prof_review_tab', 'trace'); setShowSignIn(true); }}>Sign In</button>
                      </div>
                    )}
                    {isExpanded && user && (
                      <div className="trace-category-content">
                        {g.comments.slice(0, visibleCount).map((c, ci) => {
                          const termRaw = termIdMap.get(c.termId) || '';
                          const term = cleanTerm(termRaw);
                          const hasYear = /\b20\d{2}\b/.test(term);
                          return (
                          <div
                            key={ci}
                            className={`trace-comment-bubble ${isPinned(c.comment || '', pinSnippets.trace) ? 'is-ask-pinned' : ''}`}
                          >
                            {isPinned(c.comment || '', pinSnippets.trace) && <span className="ask-pinned-label">From your question</span>}
                            <div className="trace-comment-meta">
                              {hasYear && <span className="trace-comment-term">{term}</span>}
                              {(() => {
                                const courseCode = commentCourseMap.get(c.courseId) || '';
                                return courseCode ? <span className="trace-comment-course">{courseCode}</span> : null;
                              })()}
                            </div>
                            {c.comment}
                          </div>
                          );
                        })}
                        <div className="trace-category-actions">
                          {visibleCount < g.count && (
                            <button className="trace-action-btn primary" onClick={e => showMoreComments(e, g.question)}>Show More ({g.count - visibleCount} left)</button>
                          )}
                          {visibleCount > 5 && (
                            <button className="trace-action-btn" onClick={e => { e.stopPropagation(); setVisibleCommentsPerQuestion(p => ({ ...p, [g.question]: 5 })); questionRefs.current[g.question]?.scrollIntoView({ behavior: 'smooth', block: 'center' }); }}>Show Less</button>
                          )}
                          <button className="trace-action-btn" onClick={e => { e.stopPropagation(); toggleQuestion(g.question); questionRefs.current[g.question]?.scrollIntoView({ behavior: 'smooth', block: 'center' }); }}>Collapse</button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </section>

      <Footer />
      <SignInModal open={showSignIn} onClose={() => setShowSignIn(false)} />
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