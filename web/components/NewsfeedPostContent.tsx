import type { MarketEarPost } from "@/lib/useNewsfeedPosts";
import MarkdownRenderer from "./MarkdownRenderer";
import styles from "./NewsfeedPostContent.module.css";

type Props = {
  post: Pick<MarketEarPost, "content" | "source">;
  className: string;
};

export default function NewsfeedPostContent({ post, className }: Props) {
  if (post.source) {
    if (!post.content) return null;
    return (
      <div className={`${className} ${styles.research}`}>
        {post.content ? <MarkdownRenderer content={post.content} preserveWhitespace /> : null}
      </div>
    );
  }

  if (!post.content) return null;
  return <p className={className}>{post.content}</p>;
}
