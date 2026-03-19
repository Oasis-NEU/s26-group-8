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
import ThemeToggle from '../components/ThemeToggle';
import './ProfessorCatalog.css';
import './Courses.css';

const SORT_OPTIONS = [
	{ value: 'alpha', label: 'Code A-Z' },
	{ value: 'rating', label: 'Highest Rating' },
	{ value: 'sections', label: 'Most Sections' },
	{ value: 'recent', label: 'Most Recent' },
];

interface Filters {
	q: string;
	dept: string;
	minRating: number;
	maxRating: number;
	sort: 'alpha' | 'rating' | 'sections' | 'recent';
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
		sortValue === 'rating' || sortValue === 'sections' || sortValue === 'recent' || sortValue === 'alpha'
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

	const [minRatingDraft, setMinRatingDraft] = useState(() => getFiltersFromSearchParams(searchParams).minRating);
	const [maxRatingDraft, setMaxRatingDraft] = useState(() => getFiltersFromSearchParams(searchParams).maxRating);

	const [searchSuggestions, setSearchSuggestions] = useState<CourseSuggestion[]>([]);
	const [showSearchSuggestions, setShowSearchSuggestions] = useState(false);
	const [activeSearchIndex, setActiveSearchIndex] = useState(-1);
	const searchWrapperRef = useRef<HTMLDivElement>(null);
	const searchInputRef = useRef<HTMLInputElement>(null);
	const searchDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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

	useEffect(() => {
		fetchCourseDepartments().then(setDepartments).catch(console.error);
	}, []);

	useEffect(() => {
		setLoading(true);
		fetchCoursesCatalog({
			q: filters.q || undefined,
			dept: filters.dept || undefined,
			minRating: filters.minRating > 0 ? filters.minRating : undefined,
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
	}, [filters, pageSize]);

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

	const hasActiveFilters = !!filters.q || !!filters.dept || filters.minRating > 0 || filters.maxRating < 5;

	return (
		<div className="catalog-page">
			<ThemeToggle />
			<button className="catalog-filter-toggle" onClick={() => setSidebarOpen((o) => !o)} aria-label="Toggle filters">
				<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
					<line x1="4" y1="6" x2="20" y2="6" />
					<line x1="4" y1="12" x2="20" y2="12" />
					<line x1="4" y1="18" x2="20" y2="18" />
				</svg>
				Filters
				{hasActiveFilters && <span className="filter-active-dot" />}
			</button>

			{sidebarOpen && <div className="catalog-overlay" onClick={() => setSidebarOpen(false)} />}

			<div className="catalog-layout">
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

						<div className="filter-section">
							<p className="filter-label">Sort by</p>
							<Dropdown options={SORT_OPTIONS} value={filters.sort} onChange={(v) => updateFilter('sort', v as Filters['sort'])} />
						</div>

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
							<DepartmentFilter departments={departments} selected={filters.dept} onSelect={(d) => updateFilter('dept', d)} />
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
						</div>
					</div>
				</aside>

				<main className="catalog-main">
					<div className="catalog-header">
						<h1 className="catalog-title">Courses</h1>
						<span className="catalog-count">{loading ? '…' : `${total.toLocaleString()} result${total !== 1 ? 's' : ''}`}</span>
					</div>

					<div className="catalog-search-row">
						<div className="catalog-search-wrap" ref={searchWrapperRef}>
							<input
								ref={searchInputRef}
								type="text"
								className="catalog-search"
								placeholder="Search course code or title..."
								value={filters.q}
								onChange={(e) => updateFilter('q', e.target.value)}
								onFocus={() => {
									if (searchSuggestions.length > 0) setShowSearchSuggestions(true);
								}}
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
					</div>

					<p className="catalog-disclaimer">
						Course cards currently use TRACE aggregate data only.
					</p>

					{loading ? (
						<div className="catalog-grid">
							{Array.from({ length: pageSize }).map((_, i) => (
								<div key={i} className="prof-card skeleton" />
							))}
						</div>
					) : courses.length === 0 ? (
						<div className="catalog-empty">
							<p>No courses match your filters.</p>
							<button className="clear-btn prominent" onClick={clearFilters}>
								Clear filters
							</button>
						</div>
					) : (
						<div className="catalog-grid">
							{courses.map((course) => (
								<div
									key={course.code}
									className="prof-card course-card"
									role="button"
									tabIndex={0}
									onClick={() => navigate(`/courses/${course.code.toLowerCase()}`)}
									onKeyDown={(e) => e.key === 'Enter' && navigate(`/courses/${course.code.toLowerCase()}`)}
								>
									<div className="prof-body">
										<div className="course-code">{course.code}</div>
										<h3 className="prof-name">{course.name}</h3>
										<p className="prof-dept">{course.department}</p>

										<div className="prof-rating-row">
											{course.avgRating != null ? (
												<>
													<StarRating rating={course.avgRating} size="sm" />
													<span className="prof-avg">{course.avgRating.toFixed(2)}</span>
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
}: {
	departments: string[];
	selected: string;
	onSelect: (depts: string) => void;
}) {
	const [isOpen, setIsOpen] = useState(false);
	const [search, setSearch] = useState('');
	const [menuPos, setMenuPos] = useState<{ top: number; left: number; width: number } | null>(null);
	const ref = useRef<HTMLDivElement>(null);
	const triggerRef = useRef<HTMLButtonElement>(null);

	const selectedSet = new Set(selected ? selected.split(',').map((d) => d.trim()).filter(Boolean) : []);
	const filtered = departments.filter((d) => d.toLowerCase().includes(search.toLowerCase()));

	const handleToggleDept = (dept: string) => {
		const newSet = new Set(selectedSet);
		if (newSet.has(dept)) {
			newSet.delete(dept);
		} else {
			newSet.add(dept);
		}
		onSelect(Array.from(newSet).join(','));
	};

	const handleRemovePill = (dept: string, e: React.MouseEvent) => {
		e.stopPropagation();
		const newSet = new Set(selectedSet);
		newSet.delete(dept);
		onSelect(Array.from(newSet).join(','));
	};

	const displayLabel = selectedSet.size === 0 ? 'All departments' : isOpen ? 'Select departments' : `${selectedSet.size} selected`;

	const updateMenuPos = () => {
		if (triggerRef.current) {
			const rect = triggerRef.current.getBoundingClientRect();
			setMenuPos({ top: rect.bottom + 8, left: rect.left, width: rect.width });
		}
	};

	const handleToggleOpen = () => {
		if (!isOpen) updateMenuPos();
		setIsOpen(!isOpen);
	};

	useEffect(() => {
		if (!isOpen) return;
		window.addEventListener('scroll', updateMenuPos, true);
		window.addEventListener('resize', updateMenuPos);
		return () => {
			window.removeEventListener('scroll', updateMenuPos, true);
			window.removeEventListener('resize', updateMenuPos);
		};
	}, [isOpen]);

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
			<button ref={triggerRef} className={`dept-combo-trigger ${isOpen ? 'open' : ''}`} onClick={handleToggleOpen} type="button">
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
							<span className="dept-pill-remove" aria-hidden="true">
								×
							</span>
						</button>
					))}
				</div>
			)}

			{isOpen && menuPos && (
				<div
					className="dept-combo-menu"
					style={{
						position: 'fixed',
						top: menuPos.top,
						left: menuPos.left,
						width: menuPos.width,
						right: 'auto',
					}}
				>
					<input
						className="dept-combo-search"
						type="text"
						placeholder="Search departments..."
						value={search}
						onChange={(e) => setSearch(e.target.value)}
						autoFocus
					/>
					<div className="dept-combo-list">
						{filtered.length === 0 ? (
							<p className="dept-combo-empty">No departments found</p>
						) : (
							filtered.map((d, i) => (
								<label key={d} className="dept-combo-option" style={{ animationDelay: `${i * 0.04}s` }}>
									<input type="checkbox" checked={selectedSet.has(d)} onChange={() => handleToggleDept(d)} />
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
