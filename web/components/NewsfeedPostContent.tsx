import type { MarketEarPost } from "@/lib/useNewsfeedPosts";
import MarkdownRenderer from "./MarkdownRenderer";
import styles from "./NewsfeedPostContent.module.css";

type Props = {
  post: Pick<MarketEarPost, "content" | "source">;
  className: string;
};

export default function NewsfeedPostContent({ post, className }: Props) {
  if (!post.content) return null;

  if (post.source?.kind === "dropbox") {
    return (
      <div className={`${className} ${styles.research}`}>
        <MarkdownRenderer content={post.content} preserveWhitespace />
      </div>
    );
  }

  return <p className={className}>{post.content}</p>;
}
