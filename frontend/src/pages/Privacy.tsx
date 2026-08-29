import Footer from '../components/Footer';
import './Terms.css';

const Privacy = () => {
  return (
    <div className="terms-page">
      <main className="terms-main">
        <div className="terms-shell">
          <header className="terms-header">
            <h1>Privacy Policy</h1>
            <p className="terms-meta">Effective August 12, 2026 &middot; RateMyHusky</p>
          </header>

          <div className="terms-body">
            <section className="terms-section">
              <h2>1. Introduction</h2>
              <p>
                RateMyHusky is an aggregator of professor and course information for
                Northeastern University students. This Privacy Policy describes what information
                we collect, how we use it, and the choices you have. By using RateMyHusky,
                you also agree to our{' '}
                <a href="/terms">Terms &amp; Conditions</a>.
              </p>
              <p>
                RateMyHusky is an independent student project and is not affiliated with,
                endorsed by, or officially connected to Northeastern University or RateMyProfessors.
              </p>
              <p>
                If you are a professor or instructor whose information appears on the site,
                see <em>Information About Professors &amp; Instructors</em> (Section 8) — that
                section covers what we publish about you and how to ask us to remove it.
              </p>
            </section>

            <section className="terms-section">
              <h2>2. Information We Collect</h2>
              <p>We collect only the minimum information needed to operate the service:</p>
              <ul>
                <li>
                  <strong>Google Sign-In:</strong> when you authenticate with your{' '}
                  <code>@husky.neu.edu</code> Google account, we receive your name, email
                  address, profile photo, and Google account id. Your name, email, and photo
                  are encoded in a JWT token stored in your browser and are never written to a
                  server-side database. Your Google account id is stored server-side if you
                  bookmark a professor or course — see the Bookmarks item below.
                </li>
                <li>
                  <strong>Browser preferences:</strong> your selected theme (dark/light), your
                  catalog view mode, and flags recording that you dismissed an on-page tip are
                  saved to <code>localStorage</code> on your device only and are never
                  transmitted to our servers. Which review tab you had open on a professor page
                  is held in <code>sessionStorage</code> so navigation works, and is dropped
                  when you close the tab.
                </li>
                <li>
                  <strong>IP address:</strong> your IP address is used in the moment a request
                  arrives to apply rate limits, and it is discarded when the request finishes.
                  We do not write your IP address to our database.
                </li>
                <li>
                  <strong>Feedback form:</strong> the feedback form collects a message type,
                  description, and an optional email address. Submissions are transmitted to
                  the RateMyHusky team via email and are not stored in a database. Submitted
                  information is used solely to improve the service. The form is protected by
                  a CAPTCHA challenge (see Third-Party Services). If you submit a
                  "Data Deletion Request," an email address is required so we can respond, and
                  (if you are signed in) your account identifier is included so we can locate
                  the data held for your account and delete it. That account identifier is
                  derived from your sign-in token at the time you submit and is not retained
                  beyond handling your request.
                </li>
                <li>
                  <strong>Bookmarks:</strong> when you are signed in and bookmark a professor or
                  course, we store your Google account id, the item type (professor or course),
                  the professor or course identifier, and a timestamp in our database. Each
                  account is capped at 200 bookmarks. These records are retained on our servers
                  — see <em>How We Store Your Information</em> below.
                </li>
              </ul>
              <p>
                We do <strong>not</strong> log your search queries, which professor or course
                pages you viewed, or any other browsing activity on our servers. The analytics
                providers listed under <em>Third-Party Services</em> collect their own data
                about your visit.
              </p>
            </section>

            <section className="terms-section">
              <h2>3. How We Use Your Information</h2>
              <p>The information we collect is used solely to:</p>
              <ul>
                <li>Authenticate your identity and confirm your <code>@husky.neu.edu</code> affiliation</li>
                <li>Restrict access to TRACE course evaluation comments to signed-in users</li>
                <li>Display your name and profile photo in the navigation bar while signed in</li>
                <li>Save and display the professors and courses you bookmark</li>
                <li>Apply rate limits across the site so automated traffic cannot overwhelm it</li>
              </ul>
              <p>
                We do not sell, rent, or share your personal information with third parties for
                their own use, and we do not build profiles of individual users.
              </p>
            </section>

            <section className="terms-section">
              <h2>4. How We Store Your Information</h2>
              <p>
                Your sign-in information is encoded in a JWT (JSON Web Token). During the
                Google OAuth handshake, short-lived <code>httpOnly</code> cookies are used
                to carry the flow; once complete, the resulting JWT is stored in your
                browser's <code>localStorage</code> and the handshake cookies are cleared.
                The token expires automatically after 30 days. RateMyHusky does not maintain
                user accounts or profiles; the only per-user data we store server-side is your
                bookmarks, described below.
              </p>
              <p>
                Signing out deletes the token from your browser immediately.
              </p>
              <p>
                If you bookmark a professor or course, that bookmark is stored
                server-side in our database, keyed to your Google account id, along with the
                item type, the professor or course identifier, and a timestamp. Bookmarks
                persist across sign-ins until you remove them or request deletion — see{' '}
                <em>Your Rights &amp; Choices</em>.
              </p>
              <p>
                Our database is a managed CockroachDB cluster hosted in the United States, the
                API runs on Railway, and the site is served through Vercel. Each of these
                providers processes data on our behalf under its own terms; see{' '}
                <em>Third-Party Services</em>.
              </p>
            </section>

            <section className="terms-section">
              <h2>5. Third-Party Services</h2>
              <p>RateMyHusky integrates with the following third-party services that may
                collect data under their own privacy policies:</p>
              <ul>
                <li>
                  <strong>Google OAuth 2.0</strong>: handles authentication. Your use of
                  Google sign-in is governed by{' '}
                  <a
                    href="https://policies.google.com/privacy"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Google's Privacy Policy
                  </a>.
                </li>
                <li>
                  <strong>Vercel Analytics &amp; Speed Insights</strong>: collects anonymous
                  page view and performance metrics. No personally identifiable information is
                  included. Subject to{' '}
                  <a
                    href="https://vercel.com/legal/privacy-policy"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Vercel's Privacy Policy
                  </a>.
                </li>
                <li>
                  <strong>Google Analytics</strong>: collects anonymous usage data such as page
                  views, engagement, and general geographic region to help us understand
                  how the service is used and improve it. Google Analytics may use cookies to
                  distinguish unique users. No personally identifiable information is shared
                  with Google Analytics. Subject to{' '}
                  <a
                    href="https://policies.google.com/privacy"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Google's Privacy Policy
                  </a>.
                </li>
                <li>
                  <strong>Resend</strong>: delivers email for the feedback form. When you submit
                  feedback, your message and any optional reply email address are transmitted
                  through Resend to reach our team. Subject to{' '}
                  <a
                    href="https://resend.com/legal/privacy-policy"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Resend's Privacy Policy
                  </a>.
                </li>
                <li>
                  <strong>Cloudflare Turnstile</strong>: a CAPTCHA that protects the feedback
                  form from automated abuse. It may process your IP address and browser signals
                  to verify you are human. Subject to{' '}
                  <a
                    href="https://www.cloudflare.com/privacypolicy/"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Cloudflare's Privacy Policy
                  </a>.
                </li>
                <li>
                  <strong>Vercel, Railway &amp; Cockroach Labs</strong>: host the site, the API,
                  and the database respectively. As part of normal infrastructure operation they
                  may log request data such as IP addresses and user-agent strings. Subject to{' '}
                  <a
                    href="https://vercel.com/legal/privacy-policy"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Vercel's
                  </a>
                  ,{' '}
                  <a
                    href="https://railway.com/legal/privacy"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Railway's
                  </a>
                  , and{' '}
                  <a
                    href="https://www.cockroachlabs.com/privacy/"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Cockroach Labs'
                  </a>{' '}
                  privacy policies.
                </li>
                <li>
                  <strong>RateMyProfessors, Northeastern TRACE, Northeastern faculty pages
                  &amp; Reddit</strong>: these are data sources only. We do not send any user
                  data to these services.
                </li>
              </ul>
            </section>

            <section className="terms-section">
              <h2>6. Cookies &amp; Local Storage</h2>
              <p>
                During the Google OAuth sign-in flow, short-lived <code>httpOnly</code> cookies
                are set to carry your return destination through the handshake; they are not used
                for tracking and are cleared once sign-in completes. Google Analytics may set
                cookies (e.g., <code>_ga</code>, <code>_gid</code>) to distinguish unique users
                and track anonymous session data. You can opt out of Google Analytics tracking by
                installing the{' '}
                <a
                  href="https://tools.google.com/dlpage/gaoptout"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Google Analytics Opt-out Browser Add-on
                </a>.
              </p>
              <p>
                <code>localStorage</code> is used to store your JWT session token
                (<code>auth_token</code>), your theme, your catalog view mode, and flags for
                on-page tips you have dismissed. <code>sessionStorage</code> holds the review tab
                you last opened, and is cleared when you close the tab. None of this data is
                synced to our servers.
              </p>
            </section>

            <section className="terms-section">
              <h2>7. Your Rights &amp; Choices</h2>
              <p>
                Because we keep so little data, most of your data is under your direct control.
                The rights below describe what you can request and how to exercise them:
              </p>
              <ul>
                <li>
                  <strong>Right to access:</strong> you may request a copy of the data we hold
                  that is associated with you. In practice this is limited to your bookmarks;
                  your sign-in details and preferences live only in your own browser and are not
                  accessible to us.
                </li>
                <li>
                  <strong>Right to deletion:</strong> you can <strong>sign out</strong> at any
                  time to immediately delete your JWT token from your browser, and{' '}
                  <strong>clear localStorage</strong> in your browser settings to remove your
                  session token and stored preferences. You can remove an individual bookmark at
                  any time by un-bookmarking it. To delete everything we hold for your account,
                  sign in and submit a <strong>Data Deletion Request</strong> through the
                  feedback form: an email address is required, and your signed-in account
                  identifier is included so we can verify your identity and delete the records
                  tied to your account. (You may also email{' '}
                  <a href="mailto:support@ratemyhusky.com">support@ratemyhusky.com</a>, though
                  because we never store your email address, we can only act on a request we can
                  tie to your account — submitting the form while signed in is the reliable path.)
                </li>
                <li>
                  <strong>Right to correction:</strong> your name, email, and photo come
                  directly from Google and are never stored on our servers, so corrections to
                  them are made through your Google account. Your Google account id also comes
                  from Google and cannot be edited; we store it with your bookmarks, and a Data
                  Deletion Request removes it (see Right to deletion above).
                  If you believe professor or course data displayed on the site is inaccurate,
                  you can report it through the feedback form under "Incorrect Data."
                </li>
                <li>
                  <strong>Right to raise a concern:</strong> you may contact us at any time at{' '}
                  <a href="mailto:support@ratemyhusky.com">support@ratemyhusky.com</a> with any
                  question or complaint about how your data is handled.
                </li>
              </ul>
              <p>
                Note that there is no account to delete. Once your token is cleared, no personal
                data remains in our systems except for any bookmarks, which remain until you
                request their deletion.
              </p>
            </section>

            <section className="terms-section">
              <h2>8. Information About Professors &amp; Instructors</h2>
              <p>
                Most of the personal information on RateMyHusky is about professors and
                instructors rather than about the students using the site, so it is worth
                stating plainly what we publish and where it comes from:
              </p>
              <ul>
                <li>
                  Name, department, and college, together with metrics we compute from the
                  underlying data (average rating, difficulty, would-take-again, per-term
                  history, and department comparisons).
                </li>
                <li>
                  Ratings and written reviews from RateMyProfessors, and scores and written
                  comments from Northeastern's TRACE course evaluations. TRACE comments are
                  shown only to signed-in <code>@husky.neu.edu</code> users.
                </li>
                <li>
                  A profile photo, taken from the public Northeastern faculty and college
                  directory pages that publish it.
                </li>
                <li>
                  Excerpts of public Reddit posts and comments that mention the professor, shown
                  with the subreddit and a link to the original, along with a sentiment label our
                  pipeline derives from the text. We do not display or store Reddit usernames.
                </li>
              </ul>
              <p>
                We publish no contact details, no course rosters, and nothing that is not already
                public in the sources above (except TRACE comments, which stay behind sign-in as
                Northeastern publishes them). Data is refreshed on a weekly schedule.
              </p>
              <p>
                <strong>Removal requests.</strong> If you are a professor or instructor and want
                your information taken down, email{' '}
                <a href="mailto:legal@ratemyhusky.com">legal@ratemyhusky.com</a> or use the
                feedback form. We honor these requests. Once processed, your name is added to a
                removal list that every data loader checks before writing, so a weekly refresh
                cannot reinstate you, and the rows already loaded — your page, your ratings and
                comments, your Reddit mentions, and the evidence corpus built from them — are
                deleted. Two limits are worth being straight about: we cannot remove your data
                from RateMyProfessors, TRACE, or Reddit themselves, and our own private source
                files can still contain your rows until the upstream source stops publishing
                them, though nothing published on the site can reach them. If you
                need removal from a source itself, contact that source directly.
              </p>
            </section>

            <section className="terms-section">
              <h2>9. Children's Privacy</h2>
              <p>
                RateMyHusky is intended for Northeastern University students (aged 18 and
                older) and is not directed at children. We do not knowingly collect personal
                information from anyone under the age of 18. If you believe a minor has
                provided us with personal information, please contact us at{' '}
                <a href="mailto:support@ratemyhusky.com">support@ratemyhusky.com</a>.
              </p>
            </section>

            <section className="terms-section">
              <h2>10. Changes to This Policy</h2>
              <p>
                We may update this Privacy Policy from time to time. The effective date at the
                top of this page will be updated when changes are made. Continued use of
                RateMyHusky after changes are posted constitutes your acceptance of the
                revised Policy.
              </p>
            </section>

            <section className="terms-section terms-section--last">
              <h2>11. Contact</h2>
              <p>
                If you have questions about this Privacy Policy or want to report a concern,
                please email us at{' '}
                <a href="mailto:support@ratemyhusky.com">support@ratemyhusky.com</a> or use
                the feedback form available at the bottom-right of any page on RateMyHusky.
              </p>
            </section>
          </div>
        </div>
      </main>
      <Footer />
    </div>
  );
};

export default Privacy;
