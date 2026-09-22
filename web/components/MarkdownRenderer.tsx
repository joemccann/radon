import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { normalizeTextLines } from "@/lib/utils";

// Module-scoped so element types stay stable across renders. An inline map
// gives every parent re-render new component types, which remounts every
// markdown node and wipes an in-progress text selection (iOS).
const BASE_COMPONENTS: Components = {
  p: ({ children }) => <p className="chat-markdown-p">{children}</p>,
  ul: ({ children }) => <ul className="chat-markdown-list chat-markdown-list-unordered">{children}</ul>,
  ol: ({ children }) => <ol className="chat-markdown-list chat-markdown-list-ordered">{children}</ol>,
  li: ({ children }) => <li className="chat-markdown-list-item">{children}</li>,
  table: ({ children }) => <div className="chat-table-wrap"><table className="chat-table" data-sortable-exempt="markdown">{children}</table></div>,
  thead: ({ children }) => <thead className="chat-markdown-thead">{children}</thead>,
  tbody: ({ children }) => <tbody>{children}</tbody>,
  tr: ({ children }) => <tr>{children}</tr>,
  th: ({ children }) => <th>{children}</th>,
  td: ({ children }) => <td>{children}</td>,
  blockquote: ({ children }) => <blockquote className="chat-markdown-blockquote">{children}</blockquote>,
  pre: ({ children }) => <pre className="chat-markdown-code-block">{children}</pre>,
  code: ({ children, className }) => {
    const isBlock = typeof className === "string" && className.startsWith("language-");
    if (isBlock) {
      return <code className="chat-markdown-fenced-code">{children}</code>;
    }
    return <code className="chat-markdown-inline-code">{children}</code>;
  },
  // Assistant answers can quote untrusted retrieved text (scraped
  // newsfeed bodies), and the same tool loop reads portfolio, P&L and
  // journal state. A markdown image is an outbound GET fired on render
  // with an attacker-chosen query string, so it is an exfiltration
  // channel for whatever figures the answer carries. Nothing in the
  // chat surface needs an image: render the alt text instead of loading.
  img: ({ alt }) => (
    <span className="chat-markdown-image-blocked">
      {alt ? `[image not loaded: ${alt}]` : "[image not loaded]"}
    </span>
  ),
  em: ({ children }) => <em className="chat-markdown-emphasis">{children}</em>,
  strong: ({ children }) => <strong className="chat-markdown-strong">{children}</strong>,
  h1: ({ children }) => <h1 className="chat-markdown-heading chat-markdown-heading-1">{children}</h1>,
  h2: ({ children }) => <h2 className="chat-markdown-heading chat-markdown-heading-2">{children}</h2>,
  h3: ({ children }) => <h3 className="chat-markdown-heading chat-markdown-heading-3">{children}</h3>,
  h4: ({ children }) => <h4 className="chat-markdown-heading chat-markdown-heading-4">{children}</h4>,
};

const LINKED_COMPONENTS: Components = {
  ...BASE_COMPONENTS,
  a: ({ href, children }) => (
    <a href={href ?? "#"} target="_blank" rel="noopener noreferrer" className="chat-markdown-link">
      {children}
    </a>
  ),
};

const INERT_LINK_COMPONENTS: Components = {
  ...BASE_COMPONENTS,
  a: ({ children }) => <span className="chat-markdown-link-disabled">{children}</span>,
};

type MarkdownRendererProps = {
  content: string;
  preserveWhitespace?: boolean;
  /** Render anchors as inert text. For untrusted excerpts (scraped PDF text)
   * where a clickable attacker-chosen href is a phishing surface. */
  disableLinks?: boolean;
};

export default function MarkdownRenderer({ content, preserveWhitespace = false, disableLinks = false }: MarkdownRendererProps) {
  const normalized = preserveWhitespace ? content : normalizeTextLines(content);
  // Empty input renders nothing. The decision of what to show for an in-flight
  // or empty assistant turn belongs to ChatPanel (typing indicator), not here —
  // inventing "No output." copy was the source of the streaming flash.
  if (!normalized) {
    return null;
  }

  return (
    <div className="chat-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={disableLinks ? INERT_LINK_COMPONENTS : LINKED_COMPONENTS}
      >
        {normalized}
      </ReactMarkdown>
    </div>
  );
}
