import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  fetchProfessorsCatalog,
  fetchDepartments,
  fetchSearchSuggestions,
  type CatalogProfessor,
  type ProfessorSuggestion,
} from '../api/api';
import Footer from '../components/Footer';
import Dropdown from '../components/Dropdown';
import StarRating from '../components/StarRating';
import Seo from '../components/Seo';
import { getInitials, splitProfName, stripPrefix } from '../utils/nameUtils';

import './ProfessorCatalog.css';

const SORT_OPTIONS = [
  { value: 'alpha',   label: 'A – Z' },
  { value: 'rating',  label: 'Highest Rating' },
  { value: 'comments', label: 'Most Comments' },
];

const REVIEW_SLIDER_MAX = 1000;
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
  minReviews: 0,
  maxReviews: null,
  sort:       'alpha',
  page:       1,
};

function getFiltersFromSearchParams(sp: URLSearchParams): Filters {
  const sortValue = sp.get('sort');
  const sort = sortValue === 'rating' || sortValue === 'comments' || sortValue === 'alpha'
    ? sortValue
    : 'alpha';

  const minRating = Number(sp.get('minRating') || '0');
  const maxRating = Number(sp.get('maxRating') || '5');
  const minReviews = Number(sp.get('minReviews') || '0');
  const maxReviewsRaw = sp.get('maxReviews');
  const maxReviews = maxReviewsRaw !== null ? Number(maxReviewsRaw) : null;
  const page = Number(sp.get('page') || '1');

  return {
    q:          sp.get('q') || '',
    college:    sp.get('college') || '',
    dept:       sp.get('dept') || '',
    minRating:  Number.isFinite(minRating) ? Math.max(0, Math.min(5, minRating)) : 0,
    maxRating:  Number.isFinite(maxRating) ? Math.max(0, Math.min(5, maxRating)) : 5,
    minReviews: Number.isFinite(minReviews) ? Math.max(0, Math.floor(minReviews)) : 0,
    maxReviews: maxReviews !== null && Number.isFinite(maxReviews) ? Math.max(0, Math.floor(maxReviews)) : null,
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
  if (filters.minReviews > 0) next.set('minReviews', String(filters.minReviews));
  if (filters.maxReviews !== null) next.set('maxReviews', String(filters.maxReviews));
  if (filters.sort !== 'alpha') next.set('sort', filters.sort);
  if (filters.page > 1) next.set('page', String(filters.page));
  return next;
}

function ratingColor(v: number | null): 'high' | 'mid' | 'low' | 'neutral' {
  if (v === null) return 'neutral';
  if (v >= 4) return 'high';
  if (v >= 3) return 'mid';
  return 'low';
}

export default function ProfessorCatalog() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [filters, setFilters]       = useState<Filters>(() => getFiltersFromSearchParams(searchParams));
  const [colleges]     = useState<string[]>([
    'Business', 'CAMD', 'CSSH', 'Engineering',
    'Health Sciences', 'Khoury', 'Law', 'Professional Studies', 'Science',
  ]);
  const [departments, setDepts]     = useState<string[]>([]);
  const [professors, setProfessors] = useState<CatalogProfessor[]>([]);
  const [total, setTotal]           = useState(0);
  const [totalPages, setTotalPages] = useState(1);
  const [loading, setLoading]       = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [deptOpen, setDeptOpen] = useState(false);
  const [collegeOpen, setCollegeOpen] = useState(false);

  useEffect(() => {
    const close = () => setSidebarOpen(false);
    window.addEventListener('close-filter-sidebar', close);
    return () => window.removeEventListener('close-filter-sidebar', close);
  }, []);

  useEffect(() => {
    document.body.style.overflow = sidebarOpen ? 'hidden' : '';
    if (!sidebarOpen) return () => { document.body.style.overflow = ''; };
    const handler = (e: TouchEvent) => {
      if (sidebarRef.current && !sidebarRef.current.contains(e.target as Node)) {
        setSidebarOpen(false);
      }
    };
    document.addEventListener('touchmove', handler, { passive: true });
    return () => {
      document.body.style.overflow = '';
      document.removeEventListener('touchmove', handler);
    };
  }, [sidebarOpen]);


  const [viewMode, setViewMode] = useState<'grid' | 'list'>(() => (localStorage.getItem('catalog-view') as 'grid' | 'list') || 'grid');
  const [minRatingDraft, setMinRatingDraft] = useState(() => getFiltersFromSearchParams(searchParams).minRating);
  const [maxRatingDraft, setMaxRatingDraft] = useState(() => getFiltersFromSearchParams(searchParams).maxRating);
  const [minReviewsDraft, setMinReviewsDraft] = useState(() => getFiltersFromSearchParams(searchParams).minReviews);
  const [maxReviewsDraft, setMaxReviewsDraft] = useState<number | null>(() => getFiltersFromSearchParams(searchParams).maxReviews);
  const [searchSuggestions, setSearchSuggestions] = useState<ProfessorSuggestion[]>([]);
  const [showSearchSuggestions, setShowSearchSuggestions] = useState(false);
  const [activeSearchIndex, setActiveSearchIndex] = useState(-1);
  const [isSearchFocused, setIsSearchFocused] = useState(false);
  const [searchPlaceholder, setSearchPlaceholder] = useState('');

  const professorExamples = useMemo(() => [
    "Alan Mislove", "Ravi Sundaram", "Dan Felushko",
    "Cristina Nita-Rotaru", "Stacy Marsella", "Kathleen Durant",
    "Gene Cooperman", "Benjamin Yelle", "Peter Topalov"
  ], []);

  const searchWrapperRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const searchDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sidebarRef = useRef<HTMLElement>(null);

  const [numCols, setNumCols] = useState(4);
  const [isMeasured, setIsMeasured] = useState(false);
  const gridObserverRef = useRef<ResizeObserver | null>(null);
  const gridRef = useCallback((node: HTMLDivElement | null) => {
    if (gridObserverRef.current) {
      gridObserverRef.current.disconnect();
      gridObserverRef.current = null;
    }
    if (!node) return;
    const update = () => {
      const cols = window.getComputedStyle(node).gridTemplateColumns.split(' ').length;
      setNumCols(cols);
      setIsMeasured(true);
    };
    gridObserverRef.current = new ResizeObserver(update);
    gridObserverRef.current.observe(node);
    update();
  }, []);

  // pageSize = cols × 4 rows: always even, always fills the grid completely.
  // List mode uses a fixed even count since it has no grid columns.
  const pageSize = viewMode === 'list' ? 10 : numCols * 4;

  // Re-fetch departments when college changes
  useEffect(() => {
    fetchDepartments(filters.college || undefined)
      .then(setDepts)
      .catch(console.error);
  }, [filters.college]);

  // Fetch professors when any filter changes
  useEffect(() => {
    if (viewMode === 'grid' && !isMeasured) return;
    setLoading(true);
    fetchProfessorsCatalog({
      q:          filters.q          || undefined,
      college:    filters.college    || undefined,
      dept:       filters.dept       || undefined,
      minRating:  filters.minRating  > 0 ? filters.minRating  : undefined,
      maxRating:  filters.maxRating  < 5 ? filters.maxRating  : undefined,
      minReviews: filters.minReviews > 0 ? filters.minReviews : undefined,
      maxReviews: filters.maxReviews ?? undefined,
      sort:       filters.sort as 'alpha' | 'rating' | 'comments',
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
  }, [filters, pageSize, isMeasured, viewMode]);

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
    setFilters(f => ({ ...f, college, page: 1 }));
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

  useEffect(() => {
    if (isSearchFocused || filters.q) {
      setSearchPlaceholder('Search professor name…');
      return;
    }

    let currentExampleIndex = Math.floor(Math.random() * professorExamples.length);
    let currentText = '';
    let isDeleting = false;
    let typingSpeed = 100;

    const type = () => {
      const fullText = professorExamples[currentExampleIndex];
      if (isDeleting) {
        currentText = fullText.substring(0, currentText.length - 1);
        typingSpeed = 50;
      } else {
        currentText = fullText.substring(0, currentText.length + 1);
        typingSpeed = 100;
      }
      setSearchPlaceholder(`Search for "${currentText}"`);
      if (!isDeleting && currentText === fullText) {
        isDeleting = true;
        typingSpeed = 2000;
      } else if (isDeleting && currentText === '') {
        isDeleting = false;
        currentExampleIndex = (currentExampleIndex + 1) % professorExamples.length;
        typingSpeed = 500;
      }
      timeoutId = setTimeout(type, typingSpeed);
    };

    let timeoutId = setTimeout(type, typingSpeed);
    return () => clearTimeout(timeoutId);
  }, [isSearchFocused, filters.q, professorExamples]);

  const hasActiveFilters =
    !!filters.q || !!filters.college || !!filters.dept || filters.minRating > 0 || filters.maxRating < 5 || filters.minReviews > 1 || filters.maxReviews !== null;

  const activeFilterCount =
    (filters.q ? 1 : 0) +
    (filters.college ? filters.college.split(',').filter(Boolean).length : 0) +
    (filters.dept ? filters.dept.split(',').filter(Boolean).length : 0) +
    (filters.minRating > 0 || filters.maxRating < 5 ? 1 : 0) +
    (filters.minReviews > 1 || filters.maxReviews !== null ? 1 : 0);

  return (
    <div className="catalog-page">
      <Seo
        title="Browse Northeastern Professors | RateMyHusky"
        description="Browse and filter thousands of Northeastern University professors by department, college, and rating. Compare TRACE evaluations and RateMyProfessor reviews."
        canonical="https://ratemyhusky.com/professors"
      />

      {sidebarOpen && (
        <div className="catalog-overlay" onClick={() => setSidebarOpen(false)} />
      )}

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

      <div className="catalog-layout">
        {/* ── Sidebar ── */}
        <aside ref={sidebarRef} className={`catalog-sidebar ${sidebarOpen ? 'open' : ''}`}>
          <div className={`sidebar-inner ${deptOpen || collegeOpen ? 'dept-open' : ''}`}>
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
              <p className="filter-label">
                College
                {filters.college && (
                  <button className="dept-clear-btn" onClick={() => setCollege('')}>Clear all</button>
                )}
              </p>
              <CollegeFilter
                colleges={colleges}
                selected={filters.college}
                onSelect={c => setCollege(c)}
                onOpenChange={setCollegeOpen}
              />
            </div>

            {/* Department */}
            <div className="filter-section">
              <p className="filter-label">
                Department
                {filters.dept && (
                  <button className="dept-clear-btn" onClick={() => updateFilter('dept', '')}>Clear all</button>
                )}
              </p>
              <DepartmentFilter
                departments={departments}
                selected={filters.dept}
                onSelect={d => updateFilter('dept', d)}
                onOpenChange={setDeptOpen}
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
                  {minReviewsDraft <= 0 && maxReviewsDraft === null
                    ? 'Any'
                    : minReviewsDraft <= 0
                      ? `≤ ${maxReviewsDraft}`
                      : maxReviewsDraft === null
                        ? `${minReviewsDraft}+`
                        : `${minReviewsDraft} – ${maxReviewsDraft}`}
                </span>
              </p>
              <DualRangeSlider
                min={0}
                max={REVIEW_SLIDER_MAX}
                step={50}
                valueLow={Math.min(minReviewsDraft, REVIEW_SLIDER_MAX)}
                valueHigh={Math.min(maxReviewsDraft ?? REVIEW_SLIDER_MAX, REVIEW_SLIDER_MAX)}
                onChangeLow={v => setMinReviewsDraft(v)}
                onChangeHigh={v => setMaxReviewsDraft(v >= REVIEW_SLIDER_MAX ? null : v)}
              />
              <div className="reviews-input-row">
                <input
                  type="number"
                  className="reviews-number-input"
                  min="0"
                  value={minReviewsDraft === 0 ? '' : minReviewsDraft}
                  placeholder="Min"
                  onKeyDown={e => { if (['e', 'E', '+', '-', '.'].includes(e.key)) e.preventDefault(); }}
                  onChange={e => {
                    const v = parseInt(e.target.value, 10);
                    const clamped = isNaN(v) || v < 0 ? 0 : Math.min(v, REVIEW_INPUT_MAX);
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
                  min="0"
                  value={maxReviewsDraft ?? ''}
                  placeholder="Max"
                  onKeyDown={e => { if (['e', 'E', '+', '-', '.'].includes(e.key)) e.preventDefault(); }}
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
          <div className="catalog-search-row">
            <div className="catalog-search-wrap" ref={searchWrapperRef}>
              <input
                ref={searchInputRef}
                type="text"
                className="catalog-search"
                placeholder={searchPlaceholder}
                value={filters.q}
                onChange={e => updateFilter('q', e.target.value)}
                onFocus={() => {
                  setIsSearchFocused(true);
                  if (searchSuggestions.length > 0) setShowSearchSuggestions(true);
                }}
                onBlur={() => setIsSearchFocused(false)}
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
          <button
            className={`catalog-filter-toggle${sidebarOpen ? ' open' : ''}`}
            onClick={() => setSidebarOpen(o => !o)}
            aria-label="Toggle filters"
          >
            <span className="filter-toggle-icon">
              <span className="filter-toggle-bar" />
              <span className="filter-toggle-bar" />
              <span className="filter-toggle-bar" />
            </span>
            Filters
            {activeFilterCount > 0 && (
              <span className="filter-active-badge">{activeFilterCount}</span>
            )}
          </button>
          </div>
          <p className="catalog-disclaimer">
            Professors without any rating data are not shown.{' '}
            <span>They may still have a profile if found via search.</span>
          </p>

          {loading ? (
            <div className={viewMode === 'list' ? 'catalog-list' : 'catalog-grid'} ref={viewMode === 'grid' ? gridRef : undefined}>
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
                  onClick={() => navigate(`/professors/${prof.slug}`, { state: { fromCatalog: `/professors?${searchParams.toString()}` } })}
                  onKeyDown={e =>
                    e.key === 'Enter' && navigate(`/professors/${prof.slug}`, { state: { fromCatalog: `/professors?${searchParams.toString()}` } })
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
                      {getInitials(prof.name)}
                    </span>
                  </div>
                  <div className="prof-list-info">
                    <span className="prof-list-name">{prof.name}</span>
                    <span className="prof-list-college">{prof.college}</span>
                    <span className="prof-list-dept">{prof.department}</span>
                  </div>
                  <div className="prof-list-rating-center">
                    <span className="prof-list-avg-num">{prof.avgRating != null ? prof.avgRating.toFixed(1) : 'N/A'}</span>
                    <StarRating rating={prof.avgRating ?? 0} size="sm" />
                  </div>
                  <div className="prof-list-stats">
                    <span className="prof-list-stat">{prof.totalReviews.toLocaleString()} ratings</span>
                    <span className="prof-list-stat">{prof.totalComments.toLocaleString()} comments</span>
                    <span className="prof-list-stat">{prof.wouldTakeAgainPct != null ? `${Math.round(prof.wouldTakeAgainPct)}% again` : '—'}</span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="catalog-grid" ref={gridRef}>
              {professors.map(prof => (
                <div
                  key={prof.slug}
                  className="prof-card"
                  role="button"
                  tabIndex={0}
                  onClick={() => navigate(`/professors/${prof.slug}`, { state: { fromCatalog: `/professors?${searchParams.toString()}` } })}
                  onKeyDown={e =>
                    e.key === 'Enter' && navigate(`/professors/${prof.slug}`, { state: { fromCatalog: `/professors?${searchParams.toString()}` } })
                  }
                >
                  <div className="prof-card-photo">
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
                        {getInitials(prof.name)}
                      </span>
                    </div>
                  </div>
                  <div className="prof-card-info">
                    <div className="prof-card-info-top">
                      <h3 className="prof-name">
                        {(() => {
                          const [first, rest] = splitProfName(stripPrefix(prof.name));
                          return rest ? <>{first}<br />{rest}</> : first;
                        })()}
                      </h3>
                      <div className="prof-card-rating-row">
                        <span className="prof-avg-num">{prof.avgRating != null ? prof.avgRating.toFixed(1) : 'N/A'}</span>
                        <StarRating rating={prof.avgRating ?? 0} size="sm" />
                      </div>
                    </div>
                    <span className="prof-college">{prof.college}</span>
                    <span className="prof-dept-label">{prof.department}</span>
                    <div className="prof-sub-ratings">
                      <div className="sub-rating-item" data-color={ratingColor(prof.rmpRating)}>
                        <span className="sub-rating-val">{prof.rmpRating != null ? prof.rmpRating.toFixed(1) : '—'}</span>
                        <span className="sub-rating-lbl">RMP</span>
                      </div>
                      <div className="sub-rating-item" data-color={ratingColor(prof.traceRating)}>
                        <span className="sub-rating-val">{prof.traceRating != null ? prof.traceRating.toFixed(1) : '—'}</span>
                        <span className="sub-rating-lbl">TRACE</span>
                      </div>
                    </div>
                    <div className="prof-card-footer">
                      <span className="prof-rating-count">{prof.totalReviews.toLocaleString()} ratings</span>
                      <span className="prof-rating-count prof-rating-count--center">{prof.totalComments.toLocaleString()} comments</span>
                      <span className="prof-rating-count">{prof.wouldTakeAgainPct != null ? `${Math.round(prof.wouldTakeAgainPct)}% again` : '—'}</span>
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

// ── College filter sub-component ─────────────────────────────────────────────

function CollegeFilter({
  colleges,
  selected,
  onSelect,
  onOpenChange,
}: {
  colleges: string[];
  selected: string;
  onSelect: (college: string) => void;
  onOpenChange?: (open: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const toggle = (o: boolean) => { setOpen(o); onOpenChange?.(o); };
  const [search, setSearch] = useState('');
  const ref = useRef<HTMLDivElement>(null);
  const selectedSet = useMemo(() => new Set(selected ? selected.split(',') : []), [selected]);
  const filtered = colleges.filter(c =>
    c.toLowerCase().includes(search.toLowerCase())
  );

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) toggle(false);
    };
    const scrollHandler = (e: Event) => {
      if (ref.current && ref.current.contains(e.target as Node)) return;
      toggle(false);
    };
    const closeHandler = () => toggle(false);
    document.addEventListener('mousedown', handler);
    document.addEventListener('scroll', scrollHandler, { capture: true, passive: true });
    window.addEventListener('close-filter-sidebar', closeHandler);
    return () => {
      document.removeEventListener('mousedown', handler);
      document.removeEventListener('scroll', scrollHandler, { capture: true });
      window.removeEventListener('close-filter-sidebar', closeHandler);
    };
  }, [open]);

  const toggleCollege = (c: string) => {
    const next = new Set(selectedSet);
    if (next.has(c)) next.delete(c);
    else next.add(c);
    onSelect([...next].join(','));
  };

  const label = selectedSet.size === 0
    ? 'All colleges'
    : selectedSet.size === 1
      ? [...selectedSet][0]
      : `${selectedSet.size} colleges`;

  return (
    <div className="dept-filter" ref={ref}>
      <button
        className={`dept-toggle ${open ? 'open' : ''}`}
        onClick={() => toggle(!open)}
        aria-expanded={open}
      >
        <span className="dept-toggle-label">
          {label}
        </span>
        <span className="dept-toggle-icon">
          <span className="dept-bar" />
          <span className="dept-bar" />
          <span className="dept-bar" />
        </span>
      </button>

      {open && (
        <div className="dept-dropdown">
          <input
            className="dept-search"
            type="text"
            placeholder="Search colleges…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            autoFocus
          />
          <div className="dept-list">
            {filtered.map(c => (
              <label key={c} className="dept-option">
                <input
                  type="checkbox"
                  checked={selectedSet.has(c)}
                  onChange={() => toggleCollege(c)}
                />
                <span>{c}</span>
              </label>
            ))}
            {filtered.length === 0 && (
              <p className="dept-empty">No colleges found</p>
            )}
          </div>
        </div>
      )}

      {!open && selectedSet.size > 0 && (
        <div className="filter-tags">
          {[...selectedSet].map(c => (
            <button key={c} className="filter-tag" onClick={() => toggleCollege(c)}>
              {c}
              <span className="filter-tag-x">×</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Department filter sub-component ──────────────────────────────────────────

function DepartmentFilter({
  departments,
  selected,
  onSelect,
  onOpenChange,
}: {
  departments: string[];
  selected: string;
  onSelect: (dept: string) => void;
  onOpenChange?: (open: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const toggle = (o: boolean) => { setOpen(o); onOpenChange?.(o); };
  const [search, setSearch] = useState('');
  const ref = useRef<HTMLDivElement>(null);
  const filtered = departments.filter(d =>
    d.toLowerCase().includes(search.toLowerCase())
  );
  const selectedSet = useMemo(() => new Set(selected ? selected.split(',') : []), [selected]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) toggle(false);
    };
    const scrollHandler = (e: Event) => {
      if (ref.current && ref.current.contains(e.target as Node)) return;
      toggle(false);
    };
    const closeHandler = () => toggle(false);
    document.addEventListener('mousedown', handler);
    document.addEventListener('scroll', scrollHandler, { capture: true, passive: true });
    window.addEventListener('close-filter-sidebar', closeHandler);
    return () => {
      document.removeEventListener('mousedown', handler);
      document.removeEventListener('scroll', scrollHandler, { capture: true });
      window.removeEventListener('close-filter-sidebar', closeHandler);
    };
  }, [open]);

  const toggleDept = (d: string) => {
    const next = new Set(selectedSet);
    if (next.has(d)) next.delete(d);
    else next.add(d);
    onSelect([...next].join(','));
  };

  const label = selectedSet.size === 0
    ? 'All departments'
    : selectedSet.size === 1
      ? [...selectedSet][0]
      : `${selectedSet.size} departments`;

  return (
    <div className="dept-filter" ref={ref}>
      <button
        className={`dept-toggle ${open ? 'open' : ''}`}
        onClick={() => toggle(!open)}
        aria-expanded={open}
      >
        <span className="dept-toggle-label">
          {label}
        </span>
        <span className="dept-toggle-icon">
          <span className="dept-bar" />
          <span className="dept-bar" />
          <span className="dept-bar" />
        </span>
      </button>

      {open && (
        <div className="dept-dropdown">
          <input
            className="dept-search"
            type="text"
            placeholder="Search departments…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            autoFocus
          />
          <div className="dept-list">
            {filtered.map(d => (
              <label key={d} className="dept-option">
                <input
                  type="checkbox"
                  checked={selectedSet.has(d)}
                  onChange={() => toggleDept(d)}
                />
                <span>{d}</span>
              </label>
            ))}
            {filtered.length === 0 && (
              <p className="dept-empty">No departments found</p>
            )}
          </div>
        </div>
      )}

      {!open && selectedSet.size > 0 && (
        <div className="filter-tags">
          {[...selectedSet].map(d => (
            <button key={d} className="filter-tag" onClick={() => toggleDept(d)}>
              {d}
              <span className="filter-tag-x">×</span>
            </button>
          ))}
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