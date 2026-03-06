import './StarRating.css';

interface StarRatingProps {
  rating: number;
  size?: 'sm' | 'md' | 'lg';
}

const StarRating = ({ rating, size = 'md' }: StarRatingProps) => {
  const pct = (Math.min(Math.max(rating, 0), 5) / 5) * 100;

  return (
    <span className={`star-rating star-rating-${size}`}>
      <span className="star-rating-empty">★★★★★</span>
      <span className="star-rating-filled" style={{ width: `${pct}%` }}>★★★★★</span>
    </span>
  );
};

export default StarRating;
