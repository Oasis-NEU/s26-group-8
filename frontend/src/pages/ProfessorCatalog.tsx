import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  fetchProfessorsCatalog,
  fetchColleges,
  fetchDepartments,
  fetchSearchSuggestions,
  type CatalogProfessor,
  type ProfessorSuggestion,
} from '../api/api';
import StarRating from '../components/StarRating';
import RatingBadge from '../components/RatingBadge';
import Dropdown from '../components/Dropdown';
import Footer from '../components/Footer';

import './ProfessorCatalog.css';

const SORT_OPTIONS = [
  { value: 'alpha',   label: 'A – Z' },
  { value: 'rating',  label: 'Highest Rating' },
  { value: 'reviews', label: 'Most Reviews' },
];

const REVIEW_SLIDER_MAX = 100;
const REVIEW_INPUT_MAX = 10000;

interface Filters {
  q:          string;
  college:    string;
  dept:       string;
  minRating:  number;
  maxRating:  number;
  minReviews: number;
  maxReviews: number | null;
  sort:       string;
  page:       number;
}

const DEFAULT_FILTERS: Filters = {
  q:          '',
  college:    '',
  dept:       '',
  minRating:  0,
  maxRating:  5,
  minReviews: 1,
  maxReviews: null,
  sort:       'alpha',
  page:       1,
};

function getFiltersFromSearchParams(sp: URLSearchParams): Filters {
  const sortValue = sp.get('sort');
  const sort = sortValue === 'rating' || sortValue === 'reviews' || sortValue === 'alpha'
    ? sortValue
    : 'alpha';

  const minRating = Number(sp.get('minRating') || '0');
  const maxRating = Number(sp.get('maxRating') || '5');
  const minReviews = Number(sp.get('minReviews') || '1');
  const maxReviewsRaw = sp.get('maxReviews');
  const maxReviews = maxReviewsRaw !== null ? Number(maxReviewsRaw) : null;
  const page = Number(sp.get('page') || '1');

  return {
    q:          sp.get('q') || '',
    college:    sp.get('college') || '',
    dept:       sp.get('dept') || '',
    minRating:  Number.isFinite(minRating) ? Math.max(0, Math.min(5, minRating)) : 0,
    maxRating:  Number.isFinite(maxRating) ? Math.max(0, Math.min(5, maxRating)) : 5,
    minReviews: Number.isFinite(minReviews) ? Math.max(1, Math.floor(minReviews)) : 1,
    maxReviews: maxReviews !== null && Number.isFinite(maxReviews) ? Math.max(1, Math.floor(maxReviews)) : null,
    sort,
    page:       Number.isFinite(page) ? Math.max(1, Math.floor(page)) : 1,
  };
}

function buildSearchParamsFromFilters(filters: Filters): URLSearchParams {
  const next = new URLSearchParams();
  if (filters.q) next.set('q', filters.q);
  if (filters.college) next.set('college', filters.college);
  if (filters.dept) next.set('dept', filters.dept);
  if (filters.minRating > 0) next.set('minRating', String(filters.minRating));
  if (filters.maxRating < 5) next.set('maxRating', String(filters.maxRating));
  if (filters.minReviews > 1) next.set('minReviews', String(filters.minReviews));
  if (filters.maxReviews !== null) next.set('maxReviews', String(filters.maxReviews));
  if (filters.sort !== 'alpha') next.set('sort', filters.sort);
  if (filters.page > 1) next.set('page', String(filters.page));
  return next;
}

function initials(name: string) {
  return name
    .split(' ')
    .filter(Boolean)
    .slice(0, 2)
    .map(n => n[0].toUpperCase())
    .join('');
}

export default function ProfessorCatalog() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [filters, setFilters]       = useState<Filters>(() => getFiltersFromSearchParams(searchParams));
  const [colleges, setColleges]     = useState<string[]>([]);
  const [departments, setDepts]     = useState<string[]>([]);
  const [professors, setProfessors] = useState<CatalogProfessor[]>([]);
  const [total, setTotal]           = useState(0);
  const [totalPages, setTotalPages] = useState(1);
  const [loading, setLoading]       = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [viewMode, setViewMode] = useState<'grid' | 'list'>(() => (localStorage.getItem('catalog-view') as 'grid' | 'list') || 'grid');
  const [minRatingDraft, setMinRatingDraft] = useState(() => getFiltersFromSearchParams(searchParams).minRating);
  const [maxRatingDraft, setMaxRatingDraft] = useState(() => getFiltersFromSearchParams(searchParams).maxRating);
  const [minReviewsDraft, setMinReviewsDraft] = useState(() => getFiltersFromSearchParams(searchParams).minReviews);
  const [maxReviewsDraft, setMaxReviewsDraft] = useState<number | null>(() => getFiltersFromSearchParams(searchParams).maxReviews);
  const [searchSuggestions, setSearchSuggestions] = useState<ProfessorSuggestion[]>([]);
  const [showSearchSuggestions, setShowSearchSuggestions] = useState(false);
  const [activeSearchIndex, setActiveSearchIndex] = useState(-1);

  const searchWrapperRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const searchDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Responsive page size: fewer cards on smaller screens
  const [viewportWidth, setViewportWidth] = useState(() => window.innerWidth);
  useEffect(() => {
    const onResize = () => setViewportWidth(window.innerWidth);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  const pageSize = useMemo(() => {
    if (viewportWidth <= 480) return 6;
    if (viewportWidth <= 768) return 9;
    return 20;
  }, [viewportWidth]);

  // Fetch colleges once
  useEffect(() => {
    fetchColleges().then(setColleges).catch(console.error);
  }, []);

  // Re-fetch departments when college changes
  useEffect(() => {
    fetchDepartments(filters.college || undefined)
      .then(setDepts)
      .catch(console.error);
  }, [filters.college]);

  // Fetch professors when any filter changes
  useEffect(() => {
    setLoading(true);
    fetchProfessorsCatalog({
      q:          filters.q          || undefined,
      college:    filters.college    || undefined,
      dept:       filters.dept       || undefined,
      minRating:  filters.minRating  > 0 ? filters.minRating  : undefined,
      maxRating:  filters.maxRating  < 5 ? filters.maxRating  : undefined,
      minReviews: filters.minReviews > 1 ? filters.minReviews : undefined,
      maxReviews: filters.maxReviews ?? undefined,
      sort:       filters.sort as 'alpha' | 'rating' | 'reviews',
      page:       filters.page,
      limit:      pageSize,
    })
      .then(data => {
        setProfessors(data.professors);
        setTotal(data.total);
        setTotalPages(data.totalPages);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [filters, pageSize]);

  // Keep filters in the URL so the catalog view is shareable/bookmarkable.
  useEffect(() => {
    const next = buildSearchParamsFromFilters(filters);
    if (next.toString() !== searchParams.toString()) {
      setSearchParams(next, { replace: true });
    }
  }, [filters, searchParams, setSearchParams]);

  // Keep in-memory filters synced when URL changes externally (back/forward/share links).
  useEffect(() => {
    const fromUrl = getFiltersFromSearchParams(searchParams);
    setFilters(prev => {
      if (
        prev.q === fromUrl.q &&
        prev.college === fromUrl.college &&
        prev.dept === fromUrl.dept &&
        prev.minRating === fromUrl.minRating &&
        prev.maxRating === fromUrl.maxRating &&
        prev.minReviews === fromUrl.minReviews &&
        prev.maxReviews === fromUrl.maxReviews &&
        prev.sort === fromUrl.sort &&
        prev.page === fromUrl.page
      ) {
        return prev;
      }
      return fromUrl;
    });
  }, [searchParams]);

  useEffect(() => {
    setMinRatingDraft(filters.minRating);
  }, [filters.minRating]);

  useEffect(() => {
    setMaxRatingDraft(filters.maxRating);
  }, [filters.maxRating]);

  useEffect(() => {
    setMinReviewsDraft(filters.minReviews);
  }, [filters.minReviews]);

  useEffect(() => {
    setMaxReviewsDraft(filters.maxReviews);
  }, [filters.maxReviews]);

  const updateFilter = useCallback(
    <K extends keyof Filters>(key: K, value: Filters[K]) => {
      setFilters(f => ({
        ...f,
        [key]: value,
        page: key !== 'page' ? 1 : (value as number),
      }));
    },
    []
  );

  // Debounce slider-based filter commits so the catalog doesn't reload on every drag step.
  useEffect(() => {
    const timeoutId = setTimeout(() => {
      if (minRatingDraft !== filters.minRating) {
        updateFilter('minRating', minRatingDraft);
      }
    }, 200);

    return () => clearTimeout(timeoutId);
  }, [minRatingDraft, filters.minRating, updateFilter]);

  useEffect(() => {
    const timeoutId = setTimeout(() => {
      if (maxRatingDraft !== filters.maxRating) {
        updateFilter('maxRating', maxRatingDraft);
      }
    }, 200);

    return () => clearTimeout(timeoutId);
  }, [maxRatingDraft, filters.maxRating, updateFilter]);

  useEffect(() => {
    const timeoutId = setTimeout(() => {
      if (minReviewsDraft !== filters.minReviews) {
        updateFilter('minReviews', minReviewsDraft);
      }
    }, 200);

    return () => clearTimeout(timeoutId);
  }, [minReviewsDraft, filters.minReviews, updateFilter]);

  useEffect(() => {
    const timeoutId = setTimeout(() => {
      if (maxReviewsDraft !== filters.maxReviews) {
        updateFilter('maxReviews', maxReviewsDraft);
      }
    }, 200);

    return () => clearTimeout(timeoutId);
  }, [maxReviewsDraft, filters.maxReviews, updateFilter]);

  const setCollege = useCallback((college: string) => {
    setFilters(f => ({ ...f, college, dept: '', page: 1 }));
  }, []);

  const clearFilters = () => setFilters(DEFAULT_FILTERS);

  const handleSearchSelect = useCallback((suggestion: ProfessorSuggestion) => {
    updateFilter('q', suggestion.name);
    setShowSearchSuggestions(false);
    setActiveSearchIndex(-1);
  }, [updateFilter]);

  // Homepage-style debounced professor autocomplete for catalog search.
  useEffect(() => {
    if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);

    const trimmedQuery = filters.q.trim();
    if (trimmedQuery.length < 2) {
      setSearchSuggestions([]);
      setShowSearchSuggestions(false);
      setActiveSearchIndex(-1);
      return;
    }

    searchDebounceRef.current = setTimeout(async () => {
      try {
        const results = await fetchSearchSuggestions(trimmedQuery, 'Professor');
        const professorResults = results
          .filter((result): result is ProfessorSuggestion => result.type === 'professor')
          .slice(0, 3);

        setSearchSuggestions(professorResults);
        const isFocused = document.activeElement === searchInputRef.current;
        setShowSearchSuggestions(isFocused && professorResults.length > 0);
        setActiveSearchIndex(-1);
      } catch {
        setSearchSuggestions([]);
        setShowSearchSuggestions(false);
      }
    }, 200);

    return () => {
      if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    };
  }, [filters.q]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (searchWrapperRef.current && !searchWrapperRef.current.contains(e.target as Node)) {
        setShowSearchSuggestions(false);
      }
    };

    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const hasActiveFilters =
    !!filters.q || !!filters.college || !!filters.dept || filters.minRating > 0 || filters.maxRating < 5 || filters.minReviews > 1 || filters.maxReviews !== null;

  return (
    <div className="catalog-page">
      {/* Mobile sidebar toggle */}
      <button
        className="catalog-filter-toggle"
        onClick={() => setSidebarOpen(o => !o)}
        aria-label="Toggle filters"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <line x1="4" y1="6" x2="20" y2="6" />
          <line x1="4" y1="12" x2="20" y2="12" />
          <line x1="4" y1="18" x2="20" y2="18" />
        </svg>
        Filters
        {hasActiveFilters && <span className="filter-active-dot" />}
      </button>

      {sidebarOpen && (
        <div className="catalog-overlay" onClick={() => setSidebarOpen(false)} />
      )}

      <div className="catalog-layout">
        {/* ── Sidebar ── */}
        <aside className={`catalog-sidebar ${sidebarOpen ? 'open' : ''}`}>
          <div className="sidebar-inner">
            <div className="sidebar-header">
              <span className="sidebar-title">Filters</span>
              {hasActiveFilters && (
                <button className="clear-btn" onClick={clearFilters}>
                  Clear all
                </button>
              )}
            </div>

            {/* Sort */}
            <div className="filter-section">
              <p className="filter-label">Sort by</p>
              <Dropdown
                options={SORT_OPTIONS}
                value={filters.sort}
                onChange={v => updateFilter('sort', v)}
              />
            </div>

            {/* College */}
            <div className="filter-section">
              <p className="filter-label">College</p>
              <div className="college-pills">
                <button
                  className={`college-pill ${!filters.college ? 'active' : ''}`}
                  onClick={() => setCollege('')}
                >
                  All
                </button>
                {colleges.map(c => (
                  <button
                    key={c}
                    className={`college-pill ${filters.college === c ? 'active' : ''}`}
                    onClick={() => setCollege(c)}
                  >
                    {c}
                  </button>
                ))}
              </div>
            </div>

            {/* Department */}
            <div className="filter-section">
              <p className="filter-label">
                Department
                {filters.dept && (
                  <button
                    className="clear-btn dept-clear-btn"
                    onClick={() => updateFilter('dept', '')}
                    type="button"
                  >
                    Clear all
                  </button>
                )}
              </p>
              <DepartmentFilter
                departments={departments}
                selected={filters.dept}
                onSelect={d => updateFilter('dept', d)}
              />
            </div>

            {/* Rating Range */}
            <div className="filter-section">
              <p className="filter-label">
                Rating
                <span className="slider-value">
                  {minRatingDraft === 0 && maxRatingDraft === 5
                    ? 'Any'
                    : minRatingDraft === 0
                      ? `≤ ${maxRatingDraft.toFixed(1)}`
                      : maxRatingDraft === 5
                        ? `${minRatingDraft.toFixed(1)}+`
                        : `${minRatingDraft.toFixed(1)} – ${maxRatingDraft.toFixed(1)}`}
                </span>
              </p>
              <DualRangeSlider
                min={0}
                max={5}
                step={0.5}
                valueLow={minRatingDraft}
                valueHigh={maxRatingDraft}
                onChangeLow={v => setMinRatingDraft(v)}
                onChangeHigh={v => setMaxRatingDraft(v)}
              />
              <div className="slider-tick-marks">
                {Array.from({ length: 11 }, (_, i) => {
                  const val = i * 0.5;
                  return (
                    <div key={val} className="tick-mark">
                      <div className="tick-line" />
                      <span className="tick-label">
                        {val === 0 ? '0' : Number.isInteger(val) ? String(val) : ''}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Reviews Range */}
            <div className="filter-section">
              <p className="filter-label">
                Reviews
                <span className="slider-value">
                  {minReviewsDraft <= 1 && maxReviewsDraft === null
                    ? 'Any'
                    : minReviewsDraft <= 1
                      ? `≤ ${maxReviewsDraft}`
                      : maxReviewsDraft === null
                        ? `${minReviewsDraft}+`
                        : `${minReviewsDraft} – ${maxReviewsDraft}`}
                </span>
              </p>
              <DualRangeSlider
                min={1}
                max={REVIEW_SLIDER_MAX}
                step={1}
                valueLow={minReviewsDraft}
                valueHigh={maxReviewsDraft ?? REVIEW_SLIDER_MAX}
                onChangeLow={v => setMinReviewsDraft(v)}
                onChangeHigh={v => setMaxReviewsDraft(v >= REVIEW_SLIDER_MAX ? null : v)}
              />
              <div className="reviews-input-row">
                <input
                  type="number"
                  className="reviews-number-input"
                  min="1"
                  value={minReviewsDraft === 1 ? '' : minReviewsDraft}
                  placeholder="Min"
                  onChange={e => {
                    const v = parseInt(e.target.value, 10);
                    const clamped = isNaN(v) || v < 1 ? 1 : Math.min(v, REVIEW_INPUT_MAX);
                    setMinReviewsDraft(clamped);
                    if (maxReviewsDraft !== null && clamped > maxReviewsDraft) {
                      setMaxReviewsDraft(clamped);
                    }
                  }}
                />
                <span className="reviews-separator">–</span>
                <input
                  type="number"
                  className="reviews-number-input"
                  min="1"
                  value={maxReviewsDraft ?? ''}
                  placeholder="Max"
                  onChange={e => {
                    const v = parseInt(e.target.value, 10);
                    if (isNaN(v) || e.target.value === '') {
                      setMaxReviewsDraft(null);
                    } else {
                      const clamped = Math.min(Math.max(v, minReviewsDraft), REVIEW_INPUT_MAX);
                      setMaxReviewsDraft(clamped);
                    }
                  }}
                />
              </div>
            </div>
          </div>
        </aside>

        {/* ── Main ── */}
        <main className="catalog-main">
          <div className="catalog-header">
            <h1 className="catalog-title">Professors</h1>
            <span className="catalog-count">
              {loading ? '…' : `${total.toLocaleString()} result${total !== 1 ? 's' : ''}`}
            </span>
            <div className="catalog-view-toggle">
              <button
                className={`catalog-view-btn ${viewMode === 'grid' ? 'active' : ''}`}
                onClick={() => { setViewMode('grid'); localStorage.setItem('catalog-view', 'grid'); }}
                aria-label="Grid view"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="3" width="7" height="7" /><rect x="14" y="3" width="7" height="7" />
                  <rect x="3" y="14" width="7" height="7" /><rect x="14" y="14" width="7" height="7" />
                </svg>
              </button>
              <button
                className={`catalog-view-btn ${viewMode === 'list' ? 'active' : ''}`}
                onClick={() => { setViewMode('list'); localStorage.setItem('catalog-view', 'list'); }}
                aria-label="List view"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="8" y1="6" x2="21" y2="6" /><line x1="8" y1="12" x2="21" y2="12" /><line x1="8" y1="18" x2="21" y2="18" />
                  <line x1="3" y1="6" x2="3.01" y2="6" /><line x1="3" y1="12" x2="3.01" y2="12" /><line x1="3" y1="18" x2="3.01" y2="18" />
                </svg>
              </button>
            </div>
          </div>
          <div className="catalog-search-row">
            <div className="catalog-search-wrap" ref={searchWrapperRef}>
              <input
                ref={searchInputRef}
                type="text"
                className="catalog-search"
                placeholder="Search professor name…"
                value={filters.q}
                onChange={e => updateFilter('q', e.target.value)}
                onFocus={() => {
                  if (searchSuggestions.length > 0) setShowSearchSuggestions(true);
                }}
                onKeyDown={(e) => {
                  if (!showSearchSuggestions || searchSuggestions.length === 0) return;

                  if (e.key === 'ArrowDown') {
                    e.preventDefault();
                    setActiveSearchIndex(prev => (prev < searchSuggestions.length - 1 ? prev + 1 : 0));
                  } else if (e.key === 'ArrowUp') {
                    e.preventDefault();
                    setActiveSearchIndex(prev => (prev > 0 ? prev - 1 : searchSuggestions.length - 1));
                  } else if (e.key === 'Enter' && activeSearchIndex >= 0) {
                    e.preventDefault();
                    handleSearchSelect(searchSuggestions[activeSearchIndex]);
                  } else if (e.key === 'Escape') {
                    setShowSearchSuggestions(false);
                  }
                }}
              />

              {showSearchSuggestions && (
                <ul className="catalog-search-suggestions">
                  {searchSuggestions.map((s, i) => (
                    <li
                      key={s.slug}
                      className={`catalog-suggestion-item ${i === activeSearchIndex ? 'active' : ''}`}
                      onClick={() => handleSearchSelect(s)}
                      onMouseEnter={() => setActiveSearchIndex(i)}
                    >
                      <div className="catalog-suggestion-main">
                        <span className="catalog-suggestion-name">{s.name}</span>
                        <span className="catalog-suggestion-dept">{s.dept}</span>
                      </div>
                      <span className="catalog-suggestion-rating">
                        {s.rating !== null ? s.rating.toFixed(2) : 'N/A'}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
          <p className="catalog-disclaimer">
            Professors without any rating data are not shown.{' '}
            <span>They may still have a profile if found via search.</span>
          </p>

          {loading ? (
            <div className={viewMode === 'list' ? 'catalog-list' : 'catalog-grid'}>
              {Array.from({ length: pageSize }).map((_, i) => (
                <div key={i} className={viewMode === 'list' ? 'prof-list-item skeleton' : 'prof-card skeleton'} />
              ))}
            </div>
          ) : professors.length === 0 ? (
            <div className="catalog-empty">
              <p>No professors match your filters.</p>
              <button className="clear-btn prominent" onClick={clearFilters}>
                Clear filters
              </button>
            </div>
          ) : viewMode === 'list' ? (
            <div className="catalog-list">
              {professors.map(prof => (
                <div
                  key={prof.slug}
                  className="prof-list-item"
                  role="button"
                  tabIndex={0}
                  onClick={() => navigate(`/professors/${prof.slug}`)}
                  onKeyDown={e =>
                    e.key === 'Enter' && navigate(`/professors/${prof.slug}`)
                  }
                >
                  <div className="prof-list-avatar">
                    {prof.imageUrl ? (
                      <img
                        src={prof.imageUrl}
                        alt=""
                        className="prof-avatar-img"
                        onError={(e) => {
                          const target = e.currentTarget;
                          target.style.display = 'none';
                          const fallback = target.parentElement?.querySelector('.prof-avatar-initials') as HTMLElement;
                          if (fallback) fallback.style.display = 'flex';
                        }}
                      />
                    ) : null}
                    <span
                      className="prof-avatar-initials"
                      style={prof.imageUrl ? { display: 'none' } : undefined}
                    >
                      {initials(prof.name)}
                    </span>
                  </div>
                  <div className="prof-list-info">
                    <span className="prof-list-name">{prof.name}</span>
                    <span className="prof-list-dept">{prof.department}</span>
                  </div>
                  <span className="prof-list-rating">
                    {prof.avgRating != null ? prof.avgRating.toFixed(2) : 'N/A'}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="catalog-grid">
              {professors.map(prof => (
                <div
                  key={prof.slug}
                  className="prof-card"
                  role="button"
                  tabIndex={0}
                  onClick={() => navigate(`/professors/${prof.slug}`)}
                  onKeyDown={e =>
                    e.key === 'Enter' && navigate(`/professors/${prof.slug}`)
                  }
                >
                  <div className="prof-avatar">
                    {prof.imageUrl ? (
                      <img
                        src={prof.imageUrl}
                        alt=""
                        className="prof-avatar-img"
                        onError={(e) => {
                          const target = e.currentTarget;
                          target.style.display = 'none';
                          const fallback = target.parentElement?.querySelector('.prof-avatar-initials') as HTMLElement;
                          if (fallback) fallback.style.display = 'flex';
                        }}
                      />
                    ) : null}
                    <span
                      className="prof-avatar-initials"
                      style={prof.imageUrl ? { display: 'none' } : undefined}
                    >
                      {initials(prof.name)}
                    </span>
                  </div>
                  <div className="prof-body">
                    <h3 className="prof-name">{prof.name}</h3>
                    <p className="prof-dept">{prof.department}</p>
                    <span className="prof-college">{prof.college}</span>

                    <div className="prof-rating-row">
                      {prof.avgRating != null ? (
                        <>
                          <StarRating rating={prof.avgRating} size="sm" />
                          <span className="prof-avg">{prof.avgRating.toFixed(2)}</span>
                        </>
                      ) : (
                        <span className="prof-avg na">N/A</span>
                      )}
                    </div>

                    <div className="prof-badges">
                      <RatingBadge label="RMP"   value={prof.rmpRating}   size="sm" />
                      <RatingBadge label="TRACE" value={prof.traceRating} size="sm" />
                    </div>

                    <div className="prof-meta">
                      <span>{prof.totalReviews.toLocaleString()} review{prof.totalReviews !== 1 ? 's' : ''}</span>
                      {prof.wouldTakeAgainPct != null && (
                        <>
                          <span className="meta-dot">·</span>
                          <span>{prof.wouldTakeAgainPct}% again</span>
                        </>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {!loading && totalPages > 1 && (
            <Pagination
              page={filters.page}
              totalPages={totalPages}
              onPageChange={p => updateFilter('page', p)}
            />
          )}
        </main>
      </div>

      <Footer />
    </div>
  );
}

// ── Dual-range slider sub-component ─────────────────────────────────────────

function DualRangeSlider({
  min,
  max,
  step,
  valueLow,
  valueHigh,
  onChangeLow,
  onChangeHigh,
}: {
  min: number;
  max: number;
  step: number;
  valueLow: number;
  valueHigh: number;
  onChangeLow: (v: number) => void;
  onChangeHigh: (v: number) => void;
}) {
  const [lastActive, setLastActive] = useState<'low' | 'high'>('low');
  const range = max - min;
  const lowPct = ((valueLow - min) / range) * 100;
  const highPct = ((valueHigh - min) / range) * 100;

  return (
    <div className="dual-range">
      <div className="dual-range-track">
        <div
          className="dual-range-fill"
          style={{ left: `${lowPct}%`, width: `${highPct - lowPct}%` }}
        />
      </div>
      <input
        type="range"
        className="dual-range-input"
        style={{ zIndex: lastActive === 'low' ? 4 : 3 }}
        min={min}
        max={max}
        step={step}
        value={valueLow}
        onPointerDown={() => setLastActive('low')}
        onChange={e => {
          const v = parseFloat(e.target.value);
          onChangeLow(Math.min(v, valueHigh));
        }}
      />
      <input
        type="range"
        className="dual-range-input"
        style={{ zIndex: lastActive === 'high' ? 4 : 3 }}
        min={min}
        max={max}
        step={step}
        value={valueHigh}
        onPointerDown={() => setLastActive('high')}
        onChange={e => {
          const v = parseFloat(e.target.value);
          onChangeHigh(Math.max(v, valueLow));
        }}
      />
    </div>
  );
}

// ── Department filter sub-component ──────────────────────────────────────────

function DepartmentFilter({
  departments,
  selected,
  onSelect,
}: {
  departments: string[];
  selected: string;
  onSelect: (depts: string) => void;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState('');
  const ref = useRef<HTMLDivElement>(null);

  // Parse selected departments from comma-separated string
  const selectedSet = new Set(selected ? selected.split(',').map(d => d.trim()).filter(Boolean) : []);
  const filtered = departments.filter(d =>
    d.toLowerCase().includes(search.toLowerCase())
  );

  const handleToggleDept = (dept: string) => {
    const newSet = new Set(selectedSet);
    if (newSet.has(dept)) {
      newSet.delete(dept);
    } else {
      newSet.add(dept);
    }
    const newValue = Array.from(newSet).join(',');
    onSelect(newValue);
  };

  const handleRemovePill = (dept: string, e: React.MouseEvent) => {
    e.stopPropagation();
    const newSet = new Set(selectedSet);
    newSet.delete(dept);
    const newValue = Array.from(newSet).join(',');
    onSelect(newValue);
  };

  const displayLabel = selectedSet.size === 0
    ? 'All departments'
    : isOpen
      ? 'Select departments'
      : `${selectedSet.size} selected`;

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setIsOpen(false);
        setSearch('');
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  return (
    <div className="dept-filter-combo" ref={ref}>
      <button
        className={`dept-combo-trigger ${isOpen ? 'open' : ''}`}
        onClick={() => setIsOpen(!isOpen)}
        type="button"
      >
        <span className="dept-combo-label">{displayLabel}</span>
        <svg
          className={`dept-combo-chevron ${isOpen ? 'rotated' : ''}`}
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {selectedSet.size > 0 && (
        <div className="dept-selected-pills">
          {Array.from(selectedSet).map((dept) => (
            <button
              key={dept}
              className="dept-pill"
              onClick={(e) => handleRemovePill(dept, e)}
              type="button"
              aria-label={`Remove ${dept}`}
            >
              <span className="dept-pill-text">{dept}</span>
              <span className="dept-pill-remove" aria-hidden="true">×</span>
            </button>
          ))}
        </div>
      )}

      {isOpen && (
        <div className="dept-combo-menu">
          <input
            className="dept-combo-search"
            type="text"
            placeholder="Search departments…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            autoFocus
          />
          <div className="dept-combo-list">
            {filtered.length === 0 ? (
              <p className="dept-combo-empty">No departments found</p>
            ) : (
              filtered.map((d, i) => (
                <label 
                  key={d} 
                  className="dept-combo-option"
                  style={{ animationDelay: `${i * 0.04}s` }}
                >
                  <input
                    type="checkbox"
                    checked={selectedSet.has(d)}
                    onChange={() => handleToggleDept(d)}
                  />
                  <span>{d}</span>
                </label>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Pagination sub-component ──────────────────────────────────────────────────

function Pagination({
  page,
  totalPages,
  onPageChange,
}: {
  page: number;
  totalPages: number;
  onPageChange: (p: number) => void;
}) {
  const pages: (number | '...')[] = [];

  if (totalPages <= 7) {
    for (let i = 1; i <= totalPages; i++) pages.push(i);
  } else {
    pages.push(1);
    if (page > 3) pages.push('...');
    for (
      let i = Math.max(2, page - 1);
      i <= Math.min(totalPages - 1, page + 1);
      i++
    ) {
      pages.push(i);
    }
    if (page < totalPages - 2) pages.push('...');
    pages.push(totalPages);
  }

  return (
    <div className="pagination">
      <button
        className="page-btn"
        disabled={page === 1}
        onClick={() => onPageChange(page - 1)}
      >
        ‹
      </button>
      {pages.map((p, i) =>
        p === '...' ? (
          <span key={`ell-${i}`} className="page-ellipsis">…</span>
        ) : (
          <button
            key={p}
            className={`page-btn ${p === page ? 'active' : ''}`}
            onClick={() => onPageChange(p as number)}
          >
            {p}
          </button>
        )
      )}
      <button
        className="page-btn"
        disabled={page === totalPages}
        onClick={() => onPageChange(page + 1)}
      >
        ›
      </button>
    </div>
  );
}