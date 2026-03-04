import { useState } from 'react';
import Dropdown from './Dropdown';
import './SearchBar.css';

const searchOptions = [
  { value: 'Professor', label: 'Professor' },
  { value: 'Course', label: 'Course' },
];

const SearchBar = () => {
  const [searchType, setSearchType] = useState('Professor');

  const placeholderText =
    searchType === 'Professor'
      ? 'Search by professor name...'
      : 'Search by course name or code...';

  return (
    <div className="search-wrapper">
      <div className="search-bar">
        <Dropdown
          className="search-dropdown"
          options={searchOptions}
          value={searchType}
          onChange={setSearchType}
        />

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
        />
      </div>
    </div>
  );
};

export default SearchBar;