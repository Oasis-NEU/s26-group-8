import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
	fetchCourseDepartments,
	fetchCoursesCatalog,
	fetchSearchSuggestions,
	type CatalogCourse,
	type CourseSuggestion,
} from '../api/api';
import Dropdown from '../components/Dropdown';
import Footer from '../components/Footer';
import StarRating from '../components/StarRating';
import Seo from '../components/Seo';
import './ProfessorCatalog.css';
import './Courses.css';

const SORT_OPTIONS = [
	{ value: 'alpha', label: 'A - Z' },
	{ value: 'rating', label: 'Highest Rating' },
	{ value: 'recent', label: 'Most Recent' },
];

interface Filters {
	q: string;
	dept: string;
	minRating: number;
	maxRating: number;
	sort: 'alpha' | 'rating' | 'recent';
	page: number;
}

const DEFAULT_FILTERS: Filters = {
	q: '',
	dept: '',
	minRating: 0,
	maxRating: 5,
	sort: 'alpha',
	page: 1,
};

function getFiltersFromSearchParams(sp: URLSearchParams): Filters {
	const sortValue = sp.get('sort');
	const sort =
		sortValue === 'rating' || sortValue === 'recent' || sortValue === 'alpha'
			? sortValue
			: 'alpha';

	const minRating = Number(sp.get('minRating') || '0');
	const maxRating = Number(sp.get('maxRating') || '5');
	const page = Number(sp.get('page') || '1');

	return {
		q: sp.get('q') || '',
		dept: sp.get('dept') || '',
		minRating: Number.isFinite(minRating) ? Math.max(0, Math.min(5, minRating)) : 0,
		maxRating: Number.isFinite(maxRating) ? Math.max(0, Math.min(5, maxRating)) : 5,
		sort,
		page: Number.isFinite(page) ? Math.max(1, Math.floor(page)) : 1,
	};
}

function buildSearchParamsFromFilters(filters: Filters): URLSearchParams {
	const next = new URLSearchParams();
	if (filters.q) next.set('q', filters.q);
	if (filters.dept) next.set('dept', filters.dept);
	if (filters.minRating > 0) next.set('minRating', String(filters.minRating));
	if (filters.maxRating < 5) next.set('maxRating', String(filters.maxRating));
	if (filters.sort !== 'alpha') next.set('sort', filters.sort);
	if (filters.page > 1) next.set('page', String(filters.page));
	return next;
}

export default function Courses() {
	const navigate = useNavigate();
	const [searchParams, setSearchParams] = useSearchParams();

	const [filters, setFilters] = useState<Filters>(() => getFiltersFromSearchParams(searchParams));
	const [departments, setDepartments] = useState<string[]>([]);
	const [courses, setCourses] = useState<CatalogCourse[]>([]);
	const [total, setTotal] = useState(0);
	const [totalPages, setTotalPages] = useState(1);
	const [loading, setLoading] = useState(true);
	const [sidebarOpen, setSidebarOpen] = useState(false);
	const [deptOpen, setDeptOpen] = useState(false);

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

	const [minRatingDraft, setMinRatingDraft] = useState(() => getFiltersFromSearchParams(searchParams).minRating);
	const [maxRatingDraft, setMaxRatingDraft] = useState(() => getFiltersFromSearchParams(searchParams).maxRating);

	const [searchSuggestions, setSearchSuggestions] = useState<CourseSuggestion[]>([]);
	const [showSearchSuggestions, setShowSearchSuggestions] = useState(false);
	const [activeSearchIndex, setActiveSearchIndex] = useState(-1);
	const [isSearchFocused, setIsSearchFocused] = useState(false);
	const [searchPlaceholder, setSearchPlaceholder] = useState('');

	const courseExamples = useMemo(() => [
		"CS 2500", "CS 3500", "CS 4500", "ECON 1115", "MATH 1341",
		"CY 2550", "PHYS 1161", "ACCT 1201", "MKTG 2101", "BIOL 1111"
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

	// pageSize = cols × 4 rows: always even, always fills the grid completely
	const pageSize = numCols * 4;

	useEffect(() => {
		fetchCourseDepartments().then(setDepartments).catch(console.error);
	}, []);

	useEffect(() => {
		if (!isMeasured) return;
		setLoading(true);
		fetchCoursesCatalog({
			q: filters.q || undefined,
			dept: filters.dept || undefined,
			minRating: Math.max(filters.minRating, 0.01),
			maxRating: filters.maxRating < 5 ? filters.maxRating : undefined,
			sort: filters.sort,
			page: filters.page,
			limit: pageSize,
		})
			.then((data) => {
				setCourses(data.courses);
				setTotal(data.total);
				setTotalPages(data.totalPages);
			})
			.catch(console.error)
			.finally(() => setLoading(false));
	}, [filters, pageSize, isMeasured]);

	useEffect(() => {
		const next = buildSearchParamsFromFilters(filters);
		if (next.toString() !== searchParams.toString()) {
			setSearchParams(next, { replace: true });
		}
	}, [filters, searchParams, setSearchParams]);

	useEffect(() => {
		const fromUrl = getFiltersFromSearchParams(searchParams);
		setFilters((prev) => {
			if (
				prev.q === fromUrl.q &&
				prev.dept === fromUrl.dept &&
				prev.minRating === fromUrl.minRating &&
				prev.maxRating === fromUrl.maxRating &&
				prev.sort === fromUrl.sort &&
				prev.page === fromUrl.page
			) {
				return prev;
			}
			return fromUrl;
		});
	}, [searchParams]);

	useEffect(() => setMinRatingDraft(filters.minRating), [filters.minRating]);
	useEffect(() => setMaxRatingDraft(filters.maxRating), [filters.maxRating]);

	const updateFilter = useCallback(<K extends keyof Filters>(key: K, value: Filters[K]) => {
		setFilters((f) => ({
			...f,
			[key]: value,
			page: key !== 'page' ? 1 : (value as number),
		}));
	}, []);

	useEffect(() => {
		const timeoutId = setTimeout(() => {
			if (minRatingDraft !== filters.minRating) updateFilter('minRating', minRatingDraft);
		}, 200);
		return () => clearTimeout(timeoutId);
	}, [minRatingDraft, filters.minRating, updateFilter]);

	useEffect(() => {
		const timeoutId = setTimeout(() => {
			if (maxRatingDraft !== filters.maxRating) updateFilter('maxRating', maxRatingDraft);
		}, 200);
		return () => clearTimeout(timeoutId);
	}, [maxRatingDraft, filters.maxRating, updateFilter]);

	const clearFilters = () => setFilters(DEFAULT_FILTERS);

	const handleSearchSelect = useCallback(
		(suggestion: CourseSuggestion) => {
			navigate(`/courses/${suggestion.code.toLowerCase()}`);
			setShowSearchSuggestions(false);
			setActiveSearchIndex(-1);
		},
		[navigate]
	);

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
				const results = await fetchSearchSuggestions(trimmedQuery, 'Course');
				const courseResults = results
					.filter((result): result is CourseSuggestion => result.type === 'course')
					.slice(0, 6);
				setSearchSuggestions(courseResults);
				const isFocused = document.activeElement === searchInputRef.current;
				setShowSearchSuggestions(isFocused && courseResults.length > 0);
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
			setSearchPlaceholder('Search course code or title...');
			return;
		}

		let currentExampleIndex = Math.floor(Math.random() * courseExamples.length);
		let currentText = '';
		let isDeleting = false;
		let typingSpeed = 100;

		const type = () => {
			const fullText = courseExamples[currentExampleIndex];
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
				currentExampleIndex = (currentExampleIndex + 1) % courseExamples.length;
				typingSpeed = 500;
			}
			timeoutId = setTimeout(type, typingSpeed);
		};

		let timeoutId = setTimeout(type, typingSpeed);
		return () => clearTimeout(timeoutId);
	}, [isSearchFocused, filters.q, courseExamples]);

	const hasActiveFilters = !!filters.q || !!filters.dept || filters.minRating > 0 || filters.maxRating < 5;

	return (
		<div className="catalog-page">
			<Seo
				title="Browse Northeastern Courses | RateMyHusky"
				description="Browse and filter Northeastern University courses by department and rating. See TRACE evaluation data and compare instructors for every course."
				canonical="https://ratemyhusky.com/courses"
			/>
			{sidebarOpen && <div className="catalog-overlay" onClick={() => setSidebarOpen(false)} />}

			<div className="catalog-header">
				<h1 className="catalog-title">Courses</h1>
				<span className="catalog-count">{loading ? '…' : `${total.toLocaleString()} result${total !== 1 ? 's' : ''}`}</span>
			</div>

			<div className="catalog-layout">
				<aside ref={sidebarRef} className={`catalog-sidebar ${sidebarOpen ? 'open' : ''}`}>
					<div className={`sidebar-inner ${deptOpen ? 'dept-open' : ''}`}>
						<div className="sidebar-header">
							<span className="sidebar-title">Filters</span>
							{hasActiveFilters && (
								<button className="clear-btn" onClick={clearFilters}>
									Clear all
								</button>
							)}
						</div>

						<div className="filter-section">
							<p className="filter-label">Sort by</p>
							<Dropdown options={SORT_OPTIONS} value={filters.sort} onChange={(v) => updateFilter('sort', v as Filters['sort'])} />
						</div>

						<div className="filter-section">
							<p className="filter-label">
								Department
								{filters.dept && (
									<button className="dept-clear-btn" onClick={() => updateFilter('dept', '')}>Clear all</button>
								)}
							</p>
							<DepartmentFilter departments={departments} selected={filters.dept} onSelect={(d) => updateFilter('dept', d)} onOpenChange={setDeptOpen} />
						</div>

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
												: `${minRatingDraft.toFixed(1)} - ${maxRatingDraft.toFixed(1)}`}
								</span>
							</p>
							<DualRangeSlider
								min={0}
								max={5}
								step={0.5}
								valueLow={minRatingDraft}
								valueHigh={maxRatingDraft}
								onChangeLow={(v) => setMinRatingDraft(v)}
								onChangeHigh={(v) => setMaxRatingDraft(v)}
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
					</div>
				</aside>

				<main className="catalog-main">
					<div className="catalog-search-row">
						<div className="catalog-search-wrap" ref={searchWrapperRef}>
							<input
								ref={searchInputRef}
								type="text"
								className="catalog-search"
								placeholder={searchPlaceholder}
								value={filters.q}
								onChange={(e) => updateFilter('q', e.target.value)}
								onFocus={() => {
									setIsSearchFocused(true);
									if (searchSuggestions.length > 0) setShowSearchSuggestions(true);
								}}
								onBlur={() => setIsSearchFocused(false)}
								onKeyDown={(e) => {
									if (!showSearchSuggestions || searchSuggestions.length === 0) return;
									if (e.key === 'ArrowDown') {
										e.preventDefault();
										setActiveSearchIndex((prev) => (prev < searchSuggestions.length - 1 ? prev + 1 : 0));
									} else if (e.key === 'ArrowUp') {
										e.preventDefault();
										setActiveSearchIndex((prev) => (prev > 0 ? prev - 1 : searchSuggestions.length - 1));
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
											key={s.code}
											className={`catalog-suggestion-item ${i === activeSearchIndex ? 'active' : ''}`}
											onClick={() => handleSearchSelect(s)}
											onMouseEnter={() => setActiveSearchIndex(i)}
										>
											<div className="catalog-suggestion-main">
												<span className="catalog-suggestion-name">
													<span className="course-suggestion-code">{s.code}</span> {s.name}
												</span>
												<span className="catalog-suggestion-dept">{s.dept}</span>
											</div>
										</li>
									))}
								</ul>
							)}
						</div>
					<button className={`catalog-filter-toggle${sidebarOpen ? ' open' : ''}`} onClick={() => setSidebarOpen((o) => !o)} aria-label="Toggle filters">
						<span className="filter-toggle-icon">
							<span className="filter-toggle-bar" />
							<span className="filter-toggle-bar" />
							<span className="filter-toggle-bar" />
						</span>
						Filters
						{hasActiveFilters && <span className="filter-active-dot" />}
					</button>
					</div>

					<p className="catalog-disclaimer">
						Courses without any rating data are not shown.{' '}
						<span>They may still have a page if found via search.</span>
					</p>

					{loading ? (
						<div className="catalog-grid" ref={gridRef}>
							{Array.from({ length: pageSize }).map((_, i) => (
								<div key={i} className="prof-card skeleton" />
							))}
						</div>
					) : courses.filter((c) => c.avgRating != null).length === 0 ? (
						<div className="catalog-empty">
							<p>No courses match your filters.</p>
							<button className="clear-btn prominent" onClick={clearFilters}>
								Clear filters
							</button>
						</div>
					) : (
						<div className="catalog-grid" ref={gridRef}>
							{courses.filter((c) => c.avgRating != null).map((course) => (
								<div
									key={course.code}
									className="prof-card course-card"
									role="button"
									tabIndex={0}
									onClick={() => navigate(`/courses/${course.code.toLowerCase()}`)}
									onKeyDown={(e) => e.key === 'Enter' && navigate(`/courses/${course.code.toLowerCase()}`)}
								>
									<div className="course-card-header">
										<span className="course-card-code">{course.code}</span>
									</div>
									<div className="prof-body">
										<h3 className="prof-name">{course.name}</h3>
										<p className="prof-dept">{course.department}</p>

										<div className="prof-rating-row">
											{course.avgRating != null ? (
												<>
													<span className="prof-avg-num">{course.avgRating.toFixed(2)}</span>
													<StarRating rating={course.avgRating} size="sm" />
												</>
											) : (
												<span className="prof-avg na">N/A</span>
											)}
										</div>
									</div>
								</div>
							))}
						</div>
					)}

					{!loading && totalPages > 1 && (
						<Pagination page={filters.page} totalPages={totalPages} onPageChange={(p) => updateFilter('page', p)} />
					)}
				</main>
			</div>
			<Footer />
		</div>
	);
}

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
				<div className="dual-range-fill" style={{ left: `${lowPct}%`, width: `${highPct - lowPct}%` }} />
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
				onChange={(e) => {
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
				onChange={(e) => {
					const v = parseFloat(e.target.value);
					onChangeHigh(Math.max(v, valueLow));
				}}
			/>
		</div>
	);
}

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
	const filtered = departments.filter((d) => d.toLowerCase().includes(search.toLowerCase()));
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

	const label =
		selectedSet.size === 0
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
				<span className="dept-toggle-label">{label}</span>
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
						onChange={(e) => setSearch(e.target.value)}
						autoFocus
					/>
					<div className="dept-list">
						{filtered.map((d) => (
							<label key={d} className="dept-option">
								<input
									type="checkbox"
									checked={selectedSet.has(d)}
									onChange={() => toggleDept(d)}
								/>
								<span>{d}</span>
							</label>
						))}
						{filtered.length === 0 && <p className="dept-empty">No departments found</p>}
					</div>
				</div>
			)}

			{!open && selectedSet.size > 0 && (
				<div className="filter-tags">
					{[...selectedSet].map((d) => (
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
		for (let i = Math.max(2, page - 1); i <= Math.min(totalPages - 1, page + 1); i++) pages.push(i);
		if (page < totalPages - 2) pages.push('...');
		pages.push(totalPages);
	}

	return (
		<div className="pagination">
			<button className="page-btn" disabled={page === 1} onClick={() => onPageChange(page - 1)}>
				‹
			</button>
			{pages.map((p, i) =>
				p === '...' ? (
					<span key={`ellipsis-${i}`} className="page-ellipsis">
						...
					</span>
				) : (
					<button key={p} className={`page-btn ${p === page ? 'active' : ''}`} onClick={() => onPageChange(p as number)}>
						{p}
					</button>
				)
			)}
			<button className="page-btn" disabled={page === totalPages} onClick={() => onPageChange(page + 1)}>
				›
			</button>
		</div>
	);
}