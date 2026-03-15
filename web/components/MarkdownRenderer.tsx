import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { normalizeTextLines } from "@/lib/utils";

type MarkdownRendererProps = {
  content: string;
};

export default function MarkdownRenderer({ content }: MarkdownRendererProps) {
  const normalized = normalizeTextLines(content);
  if (!normalized) {
    return <span className="chat-empty">No output.</span>;
  }

  return (
    <div className="chat-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p className="chat-markdown-p">{children}</p>,
          ul: ({ children }) => <ul className="chat-markdown-list chat-markdown-list-unordered">{children}</ul>,
          ol: ({ children }) => <ol className="chat-markdown-list chat-markdown-list-ordered">{children}</ol>,
          li: ({ children }) => <li className="chat-markdown-list-item">{children}</li>,
          table: ({ children }) => <div className="chat-table-wrap"><table className="chat-table">{children}</table></div>,
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
          a: ({ href, children }) => (
            <a href={href ?? "#"} target="_blank" rel="noopener noreferrer" className="chat-markdown-link">
              {children}
            </a>
          ),
          em: ({ children }) => <em className="chat-markdown-emphasis">{children}</em>,
          strong: ({ children }) => <strong className="chat-markdown-strong">{children}</strong>,
          h1: ({ children }) => <h1 className="chat-markdown-heading chat-markdown-heading-1">{children}</h1>,
          h2: ({ children }) => <h2 className="chat-markdown-heading chat-markdown-heading-2">{children}</h2>,
          h3: ({ children }) => <h3 className="chat-markdown-heading chat-markdown-heading-3">{children}</h3>,
          h4: ({ children }) => <h4 className="chat-markdown-heading chat-markdown-heading-4">{children}</h4>,
        }}
      >
        {normalized}
      </ReactMarkdown>
    </div>
  );
}
