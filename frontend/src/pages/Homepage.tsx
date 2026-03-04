import Navbar from '../components/Navbar';
import SearchBar from '../components/SearchBar';
import Footer from '../components/Footer';
import FeedbackTab from '../components/FeedbackTab';
import ThemeToggle from '../components/ThemeToggle';
import './Homepage.css';

const Homepage = () => {
  return (
    <div className="homepage">
      <Navbar />

      <main className="homepage-hero">
        <SearchBar />
      </main>

      <Footer />
      <FeedbackTab />
      <ThemeToggle />
    </div>
  );
};

export default Homepage;