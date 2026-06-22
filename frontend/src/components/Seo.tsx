/**
 * Seo — renders per-page <title>, meta, canonical, Open Graph / Twitter tags,
 * and optional JSON-LD structured data.
 *
 * On React 19 these elements are hoisted into <head> automatically when
 * rendered anywhere in the tree, so this component renders nothing visible
 * and needs no helmet library. Render it once data has loaded so the values
 * are accurate (e.g. inside a page after its fetch resolves).
 */

const DEFAULT_IMAGE = 'https://ratemyhusky.com/logo.jpg';

interface SeoProps {
  /** Full <title>. Include the site name yourself if you want it. */
  title: string;
  description: string;
  /** Absolute canonical URL for this page. */
  canonical: string;
  /** Absolute image URL for link previews. Falls back to the logo. */
  image?: string | null;
  /** Open Graph type. "website" for listings, "profile" for a person. */
  ogType?: 'website' | 'profile' | 'article';
  /** A JSON-LD object (or array of objects) describing the page. */
  jsonLd?: object | object[];
}

export default function Seo({
  title,
  description,
  canonical,
  image,
  ogType = 'website',
  jsonLd,
}: SeoProps) {
  const img = image || DEFAULT_IMAGE;
  const blocks = jsonLd ? (Array.isArray(jsonLd) ? jsonLd : [jsonLd]) : [];

  // Serialize JSON-LD safely for embedding in a <script> tag. Escaping "<"
  // prevents a value like "</script>" or "<!--" from breaking out of the tag
  // (the standard XSS-safe technique for JSON-in-HTML). The data here is our
  // own API output, but this makes it robust regardless of content.
  const serialize = (block: object) =>
    JSON.stringify(block).replace(/</g, '\\u003c');

  return (
    <>
      <title>{title}</title>
      <meta name="description" content={description} />
      <link rel="canonical" href={canonical} />

      {/* Open Graph (og:site_name is a constant fallback set in index.html) */}
      <meta property="og:type" content={ogType} />
      <meta property="og:title" content={title} />
      <meta property="og:description" content={description} />
      <meta property="og:url" content={canonical} />
      <meta property="og:image" content={img} />

      {/* Twitter */}
      <meta name="twitter:card" content="summary" />
      <meta name="twitter:title" content={title} />
      <meta name="twitter:description" content={description} />
      <meta name="twitter:image" content={img} />

      {blocks.map((block, i) => (
        <script
          key={i}
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: serialize(block) }}
        />
      ))}
    </>
  );
}
