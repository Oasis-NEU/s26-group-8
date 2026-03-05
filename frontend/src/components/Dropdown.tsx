import { useState, useRef, useEffect } from 'react';
import './Dropdown.css';

interface DropdownOption {
  value: string;
  label: string;
}

interface DropdownProps {
  options: DropdownOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
}

const Dropdown = ({ options, value, onChange, placeholder, className = '' }: DropdownProps) => {
  const [isOpen, setIsOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const selected = options.find((o) => o.value === value);
  const displayLabel = selected ? selected.label : placeholder || 'Select...';

  /* close on outside click */
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const handleSelect = (val: string) => {
    onChange(val);
    setIsOpen(false);
  };

  return (
    <div className={`dropdown ${className}`} ref={ref}>
      <button
        className={`dropdown-trigger ${isOpen ? 'open' : ''} ${!selected ? 'placeholder' : ''}`}
        onClick={() => setIsOpen(!isOpen)}
        type="button"
      >
        <span className="dropdown-trigger-label">{displayLabel}</span>
        <svg
          className={`dropdown-chevron ${isOpen ? 'rotated' : ''}`}
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

      {isOpen && (
        <ul className="dropdown-menu">
          {options.map((opt, i) => (
            <li
              key={opt.value}
              className={`dropdown-item ${opt.value === value ? 'active' : ''}`}
              onClick={() => handleSelect(opt.value)}
              style={{ animationDelay: `${i * 0.04}s` }}
            >
              {opt.value === value && (
                <svg className="dropdown-check" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
              )}
              {opt.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

export default Dropdown;
