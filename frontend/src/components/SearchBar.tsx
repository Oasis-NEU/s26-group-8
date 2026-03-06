import { useState, useEffect, useRef } from 'react';
import Dropdown from './Dropdown';
import { fetchSearchSuggestions } from '../api/api';
import type { SearchSuggestion } from '../api/api';
import './SearchBar.css';

const searchOptions = [
  { value: 'Professor', label: 'Professor' },
  { value: 'Course', label: 'Course' },
];

const SearchBar = () => {
  const [searchType, setSearchType] = useState('Professor');
  const [query, setQuery] = useState('');
  const [suggestions, setSuggestions] = useState<SearchSuggestion[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const placeholderText =
    searchType === 'Professor'
      ? 'Search by professor name...'
      : 'Search by course name or code...';

  // Debounced fetch
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);

    if (query.trim().length < 2) {
      setSuggestions([]);
      setShowSuggestions(false);
      return;
    }

    debounceRef.current = setTimeout(async () => {
      try {
        const results = await fetchSearchSuggestions(query, searchType);
        setSuggestions(results);
        setShowSuggestions(results.length > 0);
        setActiveIndex(-1);
      } catch {
        setSuggestions([]);
        setShowSuggestions(false);
      }
    }, 200);

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query, searchType]);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setShowSuggestions(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  // Clear suggestions when switching type
  useEffect(() => {
    setQuery('');
    setSuggestions([]);
    setShowSuggestions(false);
  }, [searchType]);

  const handleSelect = (suggestion: SearchSuggestion) => {
    setShowSuggestions(false);
    if (suggestion.type === 'professor') {
      const slug = suggestion.name.toLowerCase().replace(/[^a-z0-9]+/g, '-');
      window.location.href = `/professors/${slug}`;
    } else {
      const code = suggestion.code.toLowerCase();
      window.location.href = `/courses/${code}`;
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!showSuggestions || suggestions.length === 0) return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex((prev) => {
        const next = prev < suggestions.length - 1 ? prev + 1 : 0;
        document.querySelector(`.suggestion-item:nth-child(${next + 1})`)?.scrollIntoView({ block: 'nearest' });
        return next;
      });
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex((prev) => {
        const next = prev > 0 ? prev - 1 : suggestions.length - 1;
        document.querySelector(`.suggestion-item:nth-child(${next + 1})`)?.scrollIntoView({ block: 'nearest' });
        return next;
      });
    } else if (e.key === 'Enter' && activeIndex >= 0) {
      e.preventDefault();
      handleSelect(suggestions[activeIndex]);
    } else if (e.key === 'Escape') {
      setShowSuggestions(false);
    }
  };

  return (
    <div className="search-wrapper" ref={wrapperRef}>
      <div className="search-bar">
        <div onMouseDown={() => setShowSuggestions(false)}>
          <Dropdown
            className="search-dropdown"
            options={searchOptions}
            value={searchType}
            onChange={setSearchType}
          />
        </div>

        <div className="search-divider" />

        <span className="search-icon">
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
        </span>

        <input
          className="search-input"
          type="text"
          placeholder={placeholderText}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onFocus={() => suggestions.length > 0 && setShowSuggestions(true)}
          onKeyDown={handleKeyDown}
        />
      </div>

      {showSuggestions && (
        <ul className="search-suggestions">
          {suggestions.map((s, i) => (
            <li
              key={s.type === 'professor' ? s.name : s.code}
              className={`suggestion-item ${i === activeIndex ? 'active' : ''}`}
              onClick={() => handleSelect(s)}
              onMouseEnter={() => setActiveIndex(i)}
            >
              {s.type === 'professor' ? (
                <>
                  <div className="suggestion-main">
                    <span className="suggestion-name">{s.name}</span>
                    <span className="suggestion-dept">{s.dept}</span>
                  </div>
                  {s.rating !== null && (
                    <span className="suggestion-rating">{s.rating.toFixed(2)}</span>
                  )}
                </>
              ) : (
                <div className="suggestion-main">
                  <span className="suggestion-name">
                    <span className="suggestion-code">{s.code}</span>
                    {' '}{s.name}
                  </span>
                  <span className="suggestion-dept">{s.dept}</span>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

export default SearchBar;