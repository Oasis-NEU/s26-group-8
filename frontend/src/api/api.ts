const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:5001";

/* ---- Types ---- */
export interface Stat {
  label: string;
  value: string;
}

export interface Professor {
  name: string;
  dept: string;
  rmpRating: number | null;
  traceRating: number | null;
  avgRating: number;
  rmpReviews: number;
  traceReviews: number;
  totalReviews: number;
  url: string;
}

export interface RandomProfessor extends Professor {
  college: string;
}

/* ---- Professor page types ---- */
export interface TraceCourseScore {
  question: string;
  mean: number;
  median: number;
  stdDev: number;
  enrollment: number;
  completed: number;
  totalResponses?: number;
  count1?: number;
  count2?: number;
  count3?: number;
  count4?: number;
  count5?: number;
}

export interface TraceCourse {
  courseId: number;
  termId: number;
  termTitle: string;
  departmentName: string;
  displayName: string;
  section: string;
  enrollment: number;
  scores: TraceCourseScore[];
}

export interface ProfessorProfile {
  name: string;
  department: string;
  rmpRating: number | null;
  traceRating: number | null;
  avgRating: number;
  numRatings: number;
  wouldTakeAgainPct: number | null;
  difficulty: number | null;
  totalRatings: number;
  professorUrl: string | null;
  traceCourses: TraceCourse[];
  reviews: ProfessorReview[];
  traceComments: TraceComment[];
  imageUrl: string | null;
}

export interface ProfessorReview {
  professorName: string;
  department: string;
  overallRating: number;
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
  courseUrl: string;
  question: string;
  comment: string;
  termId: number;
}

/* ---- Fetchers ---- */
async function get<T>(path: string): Promise<T> {
  const headers: Record<string, string> = {};
  const token = localStorage.getItem('auth_token');
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  const res = await fetch(`${API_BASE}${path}`, { headers });
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
  return res.json();
}

export const fetchStats = () => get<Stat[]>("/api/stats");

export const fetchColleges = () => get<string[]>("/api/colleges");

export const fetchGoatProfessors = (college: string, limit = 10) =>
  get<Professor[]>(`/api/goat-professors?college=${encodeURIComponent(college)}&limit=${limit}`);

export const fetchRandomProfessor = () => get<RandomProfessor>("/api/random-professor");

/* ---- Professor page fetcher (single call returns everything) ---- */
export async function fetchProfessorData(slug: string): Promise<ProfessorProfile | null> {
  try {
    return await get<ProfessorProfile>(`/api/professors/${encodeURIComponent(slug)}`);
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
  wouldTakeAgainPct: number | null;
  imageUrl: string | null;
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
  totalSections: number;
  totalInstructors: number;
  totalEnrollment: number;
  totalResponses: number;
  latestTermTitle: string;
  latestTermId: number;
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
  totalSections: number;
  totalInstructors: number;
  totalEnrollment: number;
  totalResponses: number;
  latestTermTitle: string;
  latestTermId: number;
}

export interface CourseInstructorBreakdown {
  name: string;
  slug: string;
  imageUrl: string | null;
  difficulty: number | null;
  wouldTakeAgainPct: number | null;
  totalReviews: number;
  sections: number;
  totalEnrollment: number;
  totalResponses: number;
  avgRating: number | null;
}

export interface CourseSection {
  courseId: number;
  instructorId: number;
  termId: number;
  termTitle: string;
  section: string;
  instructor: string;
  enrollment: number;
  overallRating: number | null;
  totalResponses: number;
  completed: number;
}

export interface CourseQuestionScore {
  question: string;
  avgRating: number | null;
  totalResponses: number;
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
  sort?: 'alpha' | 'rating' | 'reviews';
  page?: number;
  limit?: number;
}): Promise<CatalogResponse> {
  const sp = new URLSearchParams();
  if (params.q) sp.set('q', params.q);
  if (params.college) sp.set('college', params.college);
  // Handle multiple departments (comma-separated) as OR query
  if (params.dept) {
    const depts = params.dept.split(',').map(d => d.trim()).filter(Boolean);
    depts.forEach(d => sp.append('dept', d));
  }
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

export async function fetchCourseData(code: string): Promise<CourseDetail | null> {
  try {
    return await get<CourseDetail>(`/api/courses/${encodeURIComponent(code)}`);
  } catch {
    return null;
  }
}