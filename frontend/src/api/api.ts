const API_BASE = import.meta.env.VITE_API_URL || "";

/* ---- Types ---- */
export interface Stat {
  label: string;
  value: string;
}

export interface Professor {
  name: string;
  dept: string;
  rmpRating: number | null;
  /** rmpRating projected onto TRACE's scale — the value avgRating was actually
   *  pooled from. RMP runs ~0.8 lower and 2.4x wider than TRACE, so the two raw
   *  numbers are not comparable and avgRating lands outside them often enough to
   *  read as broken (RMP 5.00 with TRACE 5.00 gave 4.99). Null unless the
   *  professor has both sources: with RMP alone avgRating already is this value,
   *  so showing it twice would imply a pooling that never happened. */
  rmpAdjusted?: number | null;
  traceRating: number | null;
  avgRating: number;
  /** Ratings: RMP ratings + TRACE overall-question responses. What the
   *  leaderboard's floor gates on, and what the ranking weights by. */
  totalReviews?: number;
  /** Rows of written text: RMP comments + TRACE comment rows. Not a subset of
   *  totalReviews and usually 2-3x larger, because TRACE stores one row per
   *  open-ended question per student. Unused by the leaderboard. */
  totalComments?: number;
}

export interface RandomProfessor {
  name: string;
  dept: string;
  college: string;
}

/* ---- Professor page types ---- */
export interface RadarDataPoint {
  metric: string;
  professor: number;
  department: number;
  profMissing: boolean;
  deptMissing: boolean;
}

export interface TraceCourse {
  courseId: number;
  termId: number;
  termTitle: string;
  departmentName: string;
  displayName: string;
  hoursPerWeek?: number | null;
  challengeWeightedSum?: number | null;
  challengeResponses?: number | null;
  overallRating?: number | null;
}

/** Per-star responses to TRACE's overall question, keyed by course code. Their
 *  sum is the number of TRACE ratings for that course — the backend no longer
 *  sends the section's `completed` (survey submitters), which was a larger,
 *  different quantity that the profile page was displaying as a rating count. */
export interface TraceRatingCounts {
  count1: number;
  count2: number;
  count3: number;
  count4: number;
  count5: number;
}

export interface ProfessorProfile {
  name: string;
  department: string;
  rmpRating: number | null;
  traceRating: number | null;
  avgRating: number;
  wouldTakeAgainPct: number | null;
  difficulty: number | null;
  totalRatings: number;
  totalComments: number;
  professorUrl: string | null;
  traceCourses: TraceCourse[];
  imageUrl: string | null;
  focusX: number;
  focusY: number;
  hoursPerWeek: number | null;
  traceRatingCounts?: Record<string, TraceRatingCounts>;
  radarData?: RadarDataPoint[] | null;
  radarTermTitle?: string | null;
  colleagues?: { name: string; slug: string; avgRating: number | null; totalRatings: number }[];
}

export interface ProfessorReviews {
  reviews: ProfessorReview[];
  traceComments: TraceComment[];
  redditMentions: RedditMention[];
}

export interface ProfessorReview {
  course: string;
  quality: number;
  difficulty: number;
  date: string;
  tags: string;
  attendance: string;
  grade: string;
  textbook: string;
  online_class: string;
  comment: string;
}

export interface TraceComment {
  courseId: number;
  question: string;
  comment: string;
  termId: number;
}

export interface RedditMention {
  body: string;
  sentiment: 'positive' | 'neutral' | 'negative' | null;
  sentiment_score: number | null;  // signed -1..+1 (negative=left, 0=neutral, positive=right)
  score: number | null;
  subreddit: string | null;
  permalink: string | null;
  created_utc: string | null;  // RFC-822 timestamp string from the backend (TIMESTAMPTZ → jsonify)
}

/* ---- Session caches (cleared on page refresh, keyed by slug/code) ---- */
const _profCache = new Map<string, ProfessorProfile>();
const _profReviewsCache = new Map<string, ProfessorReviews>();
const _courseCache = new Map<string, CourseDetail>();

type ProfessorFull = ProfessorProfile & ProfessorReviews;
const _profFullCache = new Map<string, ProfessorFull>();

/* ---- Maintenance detection ----
   While maintenance mode is on, Vercel 307-redirects /api/* to
   /maintenance.html and fetch() follows it silently. If that happened,
   send this tab to the maintenance page and report true so callers bail. */
export function maintenanceGuard(res: Response): boolean {
  if (res.redirected && new URL(res.url).pathname === '/maintenance.html') {
    window.location.replace('/maintenance.html');
    return true;
  }
  return false;
}

/* ---- Fetchers ---- */
async function get<T>(path: string): Promise<T> {
  const headers: Record<string, string> = {};
  const token = localStorage.getItem('auth_token');
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  const res = await fetch(`${API_BASE}${path}`, { headers, cache: token ? 'no-cache' : 'default' });
  if (maintenanceGuard(res)) throw new Error('Site is under maintenance');
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
  return res.json();
}

export const fetchStats = () => get<Stat[]>("/api/stats");

export const fetchColleges = () => get<string[]>("/api/colleges");

export const fetchGoatProfessors = (college: string, limit = 10) =>
  get<Professor[]>(`/api/goat-professors?college=${encodeURIComponent(college)}&limit=${limit}`);

export const fetchRandomProfessor = () => get<RandomProfessor>("/api/random-professor");

/* ---- Professor page fetchers ---- */
export async function fetchProfessorFull(slug: string): Promise<ProfessorFull | null> {
  const token = localStorage.getItem('auth_token');
  const reviewsKey = `${slug}:${token ?? 'u'}`;
  if (_profFullCache.has(reviewsKey)) return _profFullCache.get(reviewsKey)!;
  try {
    const data = await get<ProfessorFull>(`/api/professors/${encodeURIComponent(slug)}/full`);
    _profFullCache.set(reviewsKey, data);
    _profCache.set(reviewsKey, data);
    _profReviewsCache.set(reviewsKey, data);
    return data;
  } catch {
    return null;
  }
}

export async function fetchProfessorData(slug: string): Promise<ProfessorProfile | null> {
  const token = localStorage.getItem('auth_token');
  const key = `${slug}:${token ?? 'u'}`;
  if (_profCache.has(key)) return _profCache.get(key)!;
  try {
    const data = await get<ProfessorProfile>(`/api/professors/${encodeURIComponent(slug)}`);
    _profCache.set(key, data);
    return data;
  } catch {
    return null;
  }
}

export async function fetchProfessorReviews(slug: string): Promise<ProfessorReviews | null> {
  const token = localStorage.getItem('auth_token');
  const key = `${slug}:${token ?? 'u'}`;
  if (_profReviewsCache.has(key)) return _profReviewsCache.get(key)!;
  try {
    const data = await get<ProfessorReviews>(`/api/professors/${encodeURIComponent(slug)}/reviews`);
    _profReviewsCache.set(key, data);
    return data;
  } catch {
    return null;
  }
}

/* ---- Search autocomplete ---- */
export interface ProfessorSuggestion {
  type: "professor";
  name: string;
  dept: string;
  rating: number | null;
  slug: string;
}

export interface CourseSuggestion {
  type: "course";
  code: string;
  name: string;
  dept: string;
}

export type SearchSuggestion = ProfessorSuggestion | CourseSuggestion;

export const fetchSearchSuggestions = (query: string, type: string) =>
  get<SearchSuggestion[]>(`/api/search?q=${encodeURIComponent(query)}&type=${encodeURIComponent(type)}`);

/* ---- Ask mode (Reddit RAG question path) ---- */
export interface ChatSource {
  source_id: number;
  snippet: string;
  permalink: string;
  subreddit: string;
  professor_slug?: string | null;
  course_code?: string | null;
  source?: string | null;
}

export interface ChatProfessorMatch {
  name: string;
  department: string;
}

export type ChatResponse =
  | { mode: 'question'; answer: string; sources: ChatSource[]; cited?: number[]; professor_slug: string; course_code: string | null; disclaimer: string; entities?: { name: string; professor_slug?: string | null; course_code?: string | null }[] }
  | { mode: 'disambiguation'; message: string; matches: ChatProfessorMatch[] }
  | { mode: 'out_of_scope' | 'thin_data' | 'keyword'; banner?: string; message?: string; comments: unknown[]; professors: unknown[] }
  | { mode: 'course_list'; answer: string; topic?: string;
      courses: { code: string; name: string; department?: string; rating?: number | null }[];
      disclaimer: string }
  | { mode: 'error'; message: string };

/* Ask does its own fetch instead of get<T>(): get() throws on any non-2xx, but Ask must read
   the 401 body and the various 200 modes. Returns a synthetic error on network failure so the
   caller never has to try/catch. */
export async function askChat(q: string): Promise<{ status: number; body: ChatResponse }> {
  const headers: Record<string, string> = {};
  const token = localStorage.getItem('auth_token');
  if (token) headers['Authorization'] = `Bearer ${token}`;
  try {
    const res = await fetch(`${API_BASE}/api/chat?mode=question&q=${encodeURIComponent(q)}`, {
      headers,
      cache: 'no-cache',
    });
    if (maintenanceGuard(res)) throw new Error('Site is under maintenance');
    const body = (await res.json()) as ChatResponse;
    return { status: res.status, body };
  } catch {
    return { status: 0, body: { mode: 'error', message: 'Something went wrong — try again.' } };
  }
}

/* ---- Professor catalog (browse page) ---- */
export interface CatalogProfessor {
  name: string;
  slug: string;
  department: string;
  college: string;
  avgRating: number | null;
  rmpRating: number | null;
  traceRating: number | null;
  totalReviews: number;
  totalComments: number;
  wouldTakeAgainPct: number | null;
  imageUrl: string | null;
  focusX: number;
  focusY: number;
}

export interface CatalogResponse {
  professors: CatalogProfessor[];
  total: number;
  page: number;
  totalPages: number;
}

export interface CatalogCourse {
  code: string;
  name: string;
  department: string;
  avgRating: number | null;
  /* No isTopics here: the list has no use for it. A topics code arrives with
     avgRating null, which is already how the list renders it — see
     CourseSummary.isTopics, which the detail page does use. */
}

export interface CourseCatalogResponse {
  courses: CatalogCourse[];
  total: number;
  page: number;
  totalPages: number;
}

export interface CourseSummary {
  code: string;
  name: string;
  department: string;
  avgRating: number | null;
  avgEnrollment: number | null;
  latestTermTitle: string;
  ratingCount: number | null;
  /** See CatalogCourse.isTopics. avgRating and ratingCount are null when set. */
  isTopics?: boolean;
}

export interface CourseInstructorBreakdown {
  name: string;
  slug: string;
  imageUrl: string | null;
  difficulty: number | null;
  wouldTakeAgainPct: number | null;
  totalReviews: number;
  totalComments: number;
  latestTermTitle: string;
  avgRating: number | null;
  courseAvgDifficulty: number | null;
  courseAvgHoursPerWeek: number | null;
}

export interface CourseSection {
  termId: number;
  termTitle: string;
  instructor: string;
  overallRating: number | null;
  rmpRating: number | null;
}

export interface CourseQuestionScore {
  question: string;
  avgRating: number | null;
}

export interface CourseDetail {
  summary: CourseSummary;
  instructors: CourseInstructorBreakdown[];
  sections: CourseSection[];
  questionScores: CourseQuestionScore[];
}

export function fetchProfessorsCatalog(params: {
  q?: string;
  college?: string;
  dept?: string;
  minRating?: number;
  maxRating?: number;
  minReviews?: number;
  maxReviews?: number;
  sort?: 'alpha' | 'rating' | 'comments';
  page?: number;
  limit?: number;
}): Promise<CatalogResponse> {
  const sp = new URLSearchParams();
  if (params.q) sp.set('q', params.q);
  if (params.college) sp.set('college', params.college);
  if (params.dept) sp.set('dept', params.dept);
  if (params.minRating) sp.set('minRating', String(params.minRating));
  if (params.maxRating !== undefined && params.maxRating < 5) sp.set('maxRating', String(params.maxRating));
  if (params.minReviews) sp.set('minReviews', String(params.minReviews));
  if (params.maxReviews !== undefined) sp.set('maxReviews', String(params.maxReviews));
  if (params.sort) sp.set('sort', params.sort);
  if (params.page) sp.set('page', String(params.page));
  if (params.limit) sp.set('limit', String(params.limit));
  return get<CatalogResponse>(`/api/professors-catalog?${sp.toString()}`);
}

export const fetchDepartments = (college?: string) => {
  const sp = new URLSearchParams();
  if (college) sp.set('college', college);
  return get<string[]>(`/api/departments?${sp.toString()}`);
};

export const fetchCourseDepartments = () => get<string[]>('/api/course-departments');

export function fetchCoursesCatalog(params: {
  q?: string;
  dept?: string;
  minRating?: number;
  maxRating?: number;
  sort?: 'alpha' | 'rating' | 'sections' | 'recent';
  page?: number;
  limit?: number;
}): Promise<CourseCatalogResponse> {
  const sp = new URLSearchParams();
  if (params.q) sp.set('q', params.q);
  if (params.dept) sp.set('dept', params.dept);
  if (params.minRating) sp.set('minRating', String(params.minRating));
  if (params.maxRating !== undefined && params.maxRating < 5) sp.set('maxRating', String(params.maxRating));
  if (params.sort) sp.set('sort', params.sort);
  if (params.page) sp.set('page', String(params.page));
  if (params.limit) sp.set('limit', String(params.limit));
  return get<CourseCatalogResponse>(`/api/courses-catalog?${sp.toString()}`);
}

export async function submitFeedback(payload: {
  feedbackType: string;
  description: string;
  email?: string;
  turnstileToken?: string;
  accountSub?: string;
}): Promise<void> {
  const res = await fetch(`${API_BASE}/api/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (maintenanceGuard(res)) throw new Error("Site is under maintenance");
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error ?? `API ${res.status}`);
  }
}

/* ---- Bookmarks ---- */
export interface BookmarksResponse {
  professors: (CatalogProfessor & { bookmarkedAt: string })[];
  courses: (CatalogCourse & { bookmarkedAt: string })[];
}

export const fetchBookmarks = () => get<BookmarksResponse>('/api/bookmarks');

function bookmarkAuthHeaders(): Record<string, string> {
  const token = localStorage.getItem('auth_token');
  return {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

export async function addBookmark(itemType: 'professor' | 'course', itemKey: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/bookmarks`, {
    method: 'POST',
    headers: bookmarkAuthHeaders(),
    body: JSON.stringify({ itemType, itemKey }),
  });
  if (maintenanceGuard(res)) throw new Error('Site is under maintenance');
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error ?? `API ${res.status}`);
  }
}

export async function removeBookmark(itemType: 'professor' | 'course', itemKey: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/bookmarks`, {
    method: 'DELETE',
    headers: bookmarkAuthHeaders(),
    body: JSON.stringify({ itemType, itemKey }),
  });
  if (maintenanceGuard(res)) throw new Error('Site is under maintenance');
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error ?? `API ${res.status}`);
  }
}

/* ---- Department hub pages ---- */
export interface HubDepartment {
  slug: string;
  name: string;
  professorCount: number;
  avgRating: number | null;
}

export interface DepartmentsHubResponse {
  departments: HubDepartment[];
  total: number;
}

export const fetchDepartmentsHub = () => get<DepartmentsHubResponse>('/api/departments/hub');

export interface DepartmentProfessor {
  name: string;
  slug: string | null;
  avgRating: number | null;
  difficulty: number | null;
  wouldTakeAgainPct: number | null;
  totalRatings: number;
}

export interface DepartmentDetail {
  name: string;
  slug: string;
  professorCount: number;
  avgRating: number | null;
  professors: DepartmentProfessor[];
}

export async function fetchDepartmentDetail(slug: string): Promise<DepartmentDetail | null> {
  try {
    return await get<DepartmentDetail>(`/api/departments/${encodeURIComponent(slug)}`);
  } catch {
    return null;
  }
}

export async function fetchCourseData(code: string): Promise<CourseDetail | null> {
  const token = localStorage.getItem('auth_token');
  const key = `${code}:${token ?? 'u'}`;
  if (_courseCache.has(key)) return _courseCache.get(key)!;
  try {
    const data = await get<CourseDetail>(`/api/courses/${encodeURIComponent(code)}`);
    _courseCache.set(key, data);
    return data;
  } catch {
    return null;
  }
}