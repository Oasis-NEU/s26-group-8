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
}

/* ---- Fetchers ---- */
async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
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