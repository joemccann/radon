"use client";

import { X } from "lucide-react";

type NewsfeedTagBarProps = {
  selectedTags: ReadonlySet<string>;
  onRemove: (tag: string) => void;
  onClearAll: () => void;
};

export default function NewsfeedTagBar({ selectedTags, onRemove, onClearAll }: NewsfeedTagBarProps) {
  if (selectedTags.size === 0) return null;
  const tags = Array.from(selectedTags);

  return (
    <section
      className="news-feed-tag-bar"
      role="region"
      aria-label="Active tag filters"
    >
      <span className="news-feed-tag-bar-label">Filtering by</span>
      <div className="news-feed-tag-bar-list">
        {tags.map((tag) => (
          <button
            key={tag}
            type="button"
            className="news-feed-tag-bar-chip"
            onClick={() => onRemove(tag)}
            aria-label={`Remove ${tag}`}
          >
            <span>{tag}</span>
            <X size={11} aria-hidden />
          </button>
        ))}
      </div>
      <button
        type="button"
        className="news-feed-tag-bar-clear"
        onClick={onClearAll}
      >
        Clear all
      </button>
    </section>
  );
}
