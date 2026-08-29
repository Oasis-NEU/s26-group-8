"""
Data models for RateMyProfessor.com scraper.
Uses dataclasses with full type annotations.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any


@dataclass
class Review:
    """Represents a single student review on a professor's page."""

    course: Optional[str] = None
    quality: Optional[str] = None
    difficulty: Optional[str] = None
    date: Optional[str] = None
    tags: Optional[str] = None
    attendance: Optional[str] = None
    grade: Optional[str] = None
    textbook: Optional[str] = None
    online_class: Optional[str] = None
    comment: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert the review to a flat dictionary."""
        return {
            "course": self.course,
            "quality": self.quality,
            "difficulty": self.difficulty,
            "date": self.date,
            "tags": self.tags,
            "attendance": self.attendance,
            "grade": self.grade,
            "textbook": self.textbook,
            "online_class": self.online_class,
            "comment": self.comment,
        }

    def __str__(self) -> str:
        return (
            f"  [{self.date}] {self.course} — "
            f"Quality: {self.quality}, Difficulty: {self.difficulty}, "
            f"Grade: {self.grade}"
        )


@dataclass
class Professor:
    """Represents a professor scraped from RateMyProfessor.com."""

    name: Optional[str] = None
    department: Optional[str] = None
    rating: Optional[str] = None
    num_ratings: Optional[str] = None
    would_take_again_pct: Optional[str] = None
    level_of_difficulty: Optional[str] = None
    professor_url: Optional[str] = None
    graphql_id: Optional[str] = None  # base64 "Teacher-<id>" used for ratings query
    reviews: List[Review] = field(default_factory=list)
    # Did review pagination run to the end, or stop on a failed page?
    #
    # A truncated fetch is indistinguishable from a complete one by inspection:
    # both are a plausible list of reviews. It matters downstream because
    # precompute counts num_ratings and remeans `rating` from these rows rather
    # than trusting RMP's own counter, so a professor whose page 2 failed is
    # published with a real-looking rating computed from part of their reviews.
    #
    # Defaults True so a professor who is never fetched (no graphql_id, zero
    # ratings) is not reported as truncated.
    reviews_complete: bool = True

    def to_dict(self, include_reviews: bool = True) -> Dict[str, Any]:
        """Convert the professor to a dictionary.

        Args:
            include_reviews: Whether to include the full review list.

        Returns:
            Dictionary with all professor data.
        """
        data: Dict[str, Any] = {
            "name": self.name,
            "department": self.department,
            "rating": self.rating,
            "num_ratings": self.num_ratings,
            "would_take_again_pct": self.would_take_again_pct,
            "level_of_difficulty": self.level_of_difficulty,
            "professor_url": self.professor_url,
        }
        if include_reviews:
            data["reviews"] = [r.to_dict() for r in self.reviews]
        return data

    def flat_csv_row(self) -> Dict[str, Optional[str]]:
        """Return a flat dict suitable for a single CSV row (no nested reviews)."""
        return {
            "name": self.name,
            "department": self.department,
            "rating": self.rating,
            "num_ratings": self.num_ratings,
            "would_take_again_pct": self.would_take_again_pct,
            "level_of_difficulty": self.level_of_difficulty,
            "professor_url": self.professor_url,
        }

    def review_csv_rows(self) -> List[Dict[str, Any]]:
        """Return a list of flat dicts — one per review — with professor context."""
        rows: List[Dict[str, Any]] = []
        for review in self.reviews:
            row: Dict[str, Any] = {
                "professor_name": self.name,
                "department": self.department,
                "overall_rating": self.rating,
                **review.to_dict(),
            }
            rows.append(row)
        return rows

    def __str__(self) -> str:
        return (
            f"{self.name} | {self.department} | "
            f"Rating: {self.rating} | Difficulty: {self.level_of_difficulty} | "
            f"Would Take Again: {self.would_take_again_pct} | "
            f"Reviews: {len(self.reviews)}"
        )