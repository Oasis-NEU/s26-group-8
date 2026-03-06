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