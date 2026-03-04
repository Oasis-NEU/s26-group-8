import { useState } from 'react';
import logo from '../assets/logo.jpg';
import './FeedbackTab.css';

const FeedbackTab = () => {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <>
      <button className="feedback-tab" onClick={() => setIsOpen(true)}>
        Feedback
      </button>

      {isOpen && (
        <div className="feedback-overlay" onClick={() => setIsOpen(false)}>
          <div className="feedback-modal" onClick={(e) => e.stopPropagation()}>
            <button className="feedback-close" onClick={() => setIsOpen(false)}>
              ×
            </button>

            <div className="feedback-mascot">
              <img src={logo} alt="RateMyHusky Mascot" className="feedback-mascot-img" />
            </div>

            <h2 className="feedback-title">Feedback Form</h2>
            <p className="feedback-subtitle">
              Found a bug? RateMyHusky's #1 fan? Let our devs know through this form.
            </p>

            <label className="feedback-label">
              Type of Feedback <span className="feedback-required">*</span>
            </label>
            <select className="feedback-select" defaultValue="">
              <option value="" disabled>Select Feedback Type</option>
              <option value="bug">Bug Report</option>
              <option value="feature">Feature Request</option>
              <option value="general">General Feedback</option>
            </select>

            <label className="feedback-label">
              Description <span className="feedback-required">*</span>
            </label>
            <textarea
              className="feedback-textarea"
              placeholder="Say more about bugs, suggestions, etc."
              rows={4}
            />

            <label className="feedback-label">
              Email (Optional)
            </label>
            <input
              className="feedback-input"
              type="email"
              placeholder="How should we contact you?"
            />

            <button className="feedback-submit">Submit</button>
          </div>
        </div>
      )}
    </>
  );
};

export default FeedbackTab;