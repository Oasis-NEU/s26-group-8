import { useState } from 'react';
import logo from '../assets/logo.jpg';
import Dropdown from './Dropdown';
import './FeedbackTab.css';

const feedbackOptions = [
  { value: 'bug', label: 'Bug Report' },
  { value: 'feature', label: 'Feature Request' },
  { value: 'general', label: 'General Feedback' },
];

const FeedbackTab = () => {
  const [isOpen, setIsOpen] = useState(false);
  const [feedbackType, setFeedbackType] = useState('');
  const [description, setDescription] = useState('');
  const [email, setEmail] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = () => {
    if (!feedbackType || !description.trim()) {
      setError('Please select a feedback type and enter a description.');
      return;
    }
    setError('');
    setSubmitted(true);
  };

  const handleClose = () => {
    setIsOpen(false);
    // Reset after close animation
    setTimeout(() => {
      setSubmitted(false);
      setFeedbackType('');
      setDescription('');
      setEmail('');
      setError('');
    }, 300);
  };

  return (
    <>
      <button className="feedback-tab" onClick={() => setIsOpen(true)}>
        Feedback
      </button>

      {isOpen && (
        <div className="feedback-overlay" onClick={handleClose}>
          <div
            className={`feedback-modal ${submitted ? 'feedback-modal-shrink' : ''}`}
            onClick={(e) => e.stopPropagation()}
          >
            <button className="feedback-close" onClick={handleClose}>
              ×
            </button>

            {submitted ? (
              <div className="feedback-success">
                <div className="feedback-success-icon">✓</div>
                <h2 className="feedback-title">Thank You!</h2>
                <p className="feedback-success-msg">
                  We've received your feedback and appreciate you taking the time to help us improve RateMyHusky.
                </p>
                <button className="feedback-done-btn" onClick={handleClose}>
                  Done
                </button>
              </div>
            ) : (
              <>
                <div className="feedback-mascot">
                  <img src={logo} alt="RateMyHusky Mascot" className="feedback-mascot-img" />
                </div>

                <h2 className="feedback-title">Feedback Form</h2>
                <p className="feedback-subtitle">
                  Found a bug? RateMyHusky's #1 fan? Let our devs know through this form.
                </p>

                {error && <p className="feedback-error">{error}</p>}

                <label className="feedback-label">
                  Type of Feedback <span className="feedback-required">*</span>
                </label>
                <Dropdown
                  className="feedback-dropdown"
                  options={feedbackOptions}
                  value={feedbackType}
                  onChange={setFeedbackType}
                  placeholder="Select Feedback Type"
                />

                <label className="feedback-label">
                  Description <span className="feedback-required">*</span>
                </label>
                <textarea
                  className="feedback-textarea"
                  placeholder="Say more about bugs, suggestions, etc."
                  rows={4}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />

                <label className="feedback-label">
                  Email (Optional)
                </label>
                <input
                  className="feedback-input"
                  type="email"
                  placeholder="How should we contact you?"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />

                <button className="feedback-submit" onClick={handleSubmit}>Submit</button>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
};

export default FeedbackTab;