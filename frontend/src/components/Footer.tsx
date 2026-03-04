/*
  Footer component
  - Displays branding, resources, affiliations, and legal links
  - Responsive design for desktop and mobile
*/

import logo from '../assets/logo.jpg';
import './Footer.css';

const Footer = () => {
  return (
    <footer className="footer">
      <div className="footer-top">
        <div className="footer-brand">
          <img src={logo} alt="RateMyHusky" className="footer-brand-logo" />
          <div className="footer-brand-name">
            <span>Rate</span>MyHusky
          </div>
        </div>

        <div className="footer-columns">
          <div className="footer-col">
            <h4>Resources</h4>
            <ul>
              <li><a href="/changelog">Changelog</a></li>
              <li><a href="/faq">FAQs</a></li>
            </ul>
          </div>

          <div className="footer-col">
            <h4>Affiliations</h4>
            <ul>
              <li><a href="https://sandboxneu.com" target="_blank" rel="noreferrer">Sandbox</a></li>
              <li><a href="https://graduatenu.com" target="_blank" rel="noreferrer">GraduateNU</a></li>
              <li><a href="https://cooper.northeastern.edu" target="_blank" rel="noreferrer">Cooper</a></li>
            </ul>
          </div>
        </div>
      </div>

      <div className="footer-bottom">
        <span>
          © {new Date().getFullYear()} RateMyHusky. Made with{' '}
          <span role="img" aria-label="heart">❤️</span> by UX Designers and
          Developers of{' '}
          <a
            href="https://sandboxneu.com"
            target="_blank"
            rel="noreferrer"
          >
            Sandbox
          </a>{' '}
          in Boston, MA.
        </span>

        <div className="footer-bottom-links">
          <a href="/terms">Terms &amp; Conditions</a>
          <a href="/privacy">Privacy Policy</a>
        </div>
      </div>
    </footer>
  );
};

export default Footer;