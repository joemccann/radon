import type { MarketEarPost } from "@/lib/useNewsfeedPosts";
import MarkdownRenderer from "./MarkdownRenderer";
import styles from "./NewsfeedPostContent.module.css";

type Props = {
  post: Pick<MarketEarPost, "content" | "source">;
  className: string;
};

export default function NewsfeedPostContent({ post, className }: Props) {
  const summary = post.source?.kind === "marketdesk" ? post.source.summary ?? [] : [];
  const summaryList = summary.length > 0
    ? <ul aria-label="Summary" data-testid="research-summary" className={styles.summary}>{summary.map((bullet, index) => <li key={index}>{bullet}</li>)}</ul>
    : null;

  if (post.source) {
    if (!post.content && !summaryList) return null;
    return (
      <div className={`${className} ${styles.research}`}>
        {post.content ? <MarkdownRenderer content={post.content} preserveWhitespace /> : null}
        {summaryList}
      </div>
    );
  }

  if (!post.content) return null;
  return <p className={className}>{post.content}</p>;
}
